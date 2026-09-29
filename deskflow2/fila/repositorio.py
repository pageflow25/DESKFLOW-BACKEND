"""Todo o SQL da fila. Nenhuma outra camada escreve `fila_*` direto.

Duas invariantes que valem para o arquivo inteiro:

1. **Nenhuma transação fica aberta durante uma chamada externa.** O claim
   commita, a chamada acontece fora, o lease cobre a janela. É a propriedade
   boa que o DESKFLOW já tinha e que o motor preserva.
2. **Nenhum número mágico de status.** Todo id vem do `Catalogo`, que resolveu
   `codigo -> id` uma vez no start.

O DESKFLOW só escreve `fila_*`. Nenhuma tabela de domínio é tocada aqui.
"""

import logging
import socket
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from .catalogo import (
    AGUARDANDO_CALLBACK,
    CONCLUIDO,
    DLQ,
    EXECUTANDO,
    FALHOU,
    INCERTO,
    PENDENTE,
    RESERVADO,
    Catalogo,
)
from .modelos import ItemReivindicado

logger = logging.getLogger(__name__)

# Namespaces dos advisory locks. Sempre `xact` (escopo de transação): o banco
# responde pelo pooler de transação do Supabase (6543), onde lock de sessão e
# LISTEN/NOTIFY não funcionam.
NS_CHAVE_BLOQUEIO = 7301
NS_TIPO = 7302
NS_REAPER = 7303


# --- Claim ------------------------------------------------------------------

# Um roundtrip. As cinco CTEs, em ordem:
#
# `candidatos` — a fila propriamente dita: pendentes da classe/destino, já
#   disponíveis (`disponivel_em <= now()` cobre agendamento e backoff no mesmo
#   predicado), de tipo ativo, com orçamento de tentativas sobrando. O
#   `tentativa < max_tentativas` não é enfeite: o claim faz `tentativa + 1` e o
#   CHECK `ck_fila_processamento_tentativas` recusaria a linha estourada.
#   `FOR UPDATE OF f SKIP LOCKED` pega a janela sem esperar por concorrente.
#
# `livres` — as duas travas, avaliadas SÓ sobre linhas já travadas pelo passo
#   anterior:
#   * `chave_bloqueio`: o `NOT EXISTS` é a trava DURÁVEL — nenhum item é
#     reivindicado enquanto outro com a mesma chave estiver em status que
#     `ocupa_worker`, e isso vale pela execução inteira, inclusive com a
#     chamada externa em voo. O `pg_try_advisory_xact_lock` fecha a fresta
#     entre duas transações de claim simultâneas (inclusive de instâncias
#     diferentes e de classes diferentes), que sozinhas veriam as duas o
#     `NOT EXISTS` verdadeiro. `try_` nunca bloqueia, logo nunca dá deadlock.
#   * `concorrencia_maxima`: mesmo raciocínio — a contagem é a regra, o lock
#     por tipo só serializa claims concorrentes para a contagem ser exata.
#
# `ordenados` — dentro de UM lote, só o primeiro de cada `chave_bloqueio`
#   entra, e a posição dentro do tipo (`ordem_tipo`) é o que soma com a
#   ocupação já existente para respeitar o teto.
#
# `escolhidos` — aplica o teto por tipo e corta no tamanho do lote.
#
# O `UPDATE` marca `reservado` (claim feito, chamada ainda não saiu) — e não
# `executando` — porque é essa distinção que permite ao reaper devolver com
# segurança o que ainda não chegou a sair.
SQL_CLAIM = """
WITH candidatos AS (
    SELECT f.id, f.tipo_codigo, f.chave_bloqueio, f.prioridade, f.disponivel_em
      FROM fila_processamento f
      JOIN fila_processamento_tipos t ON t.codigo = f.tipo_codigo
     WHERE f.status_id = :pendente
       AND f.classe = :classe
       AND f.destino = :destino
       AND f.disponivel_em <= now()
       AND f.tentativa < f.max_tentativas
       AND t.ativo
     ORDER BY f.prioridade DESC, f.disponivel_em, f.id
     LIMIT :janela
     FOR UPDATE OF f SKIP LOCKED
),
livres AS (
    SELECT c.*
      FROM candidatos c
      JOIN fila_processamento_tipos t ON t.codigo = c.tipo_codigo
     WHERE (
            c.chave_bloqueio IS NULL
            OR (NOT EXISTS (
                    SELECT 1
                      FROM fila_processamento b
                      JOIN fila_processamento_status s ON s.id = b.status_id
                     WHERE b.chave_bloqueio = c.chave_bloqueio
                       AND b.id <> c.id
                       AND s.ocupa_worker
                )
                AND pg_try_advisory_xact_lock(:ns_chave, hashtext(c.chave_bloqueio)))
           )
       AND (
            t.concorrencia_maxima IS NULL
            OR pg_try_advisory_xact_lock(:ns_tipo, hashtext(c.tipo_codigo))
           )
),
ordenados AS (
    SELECT l.*,
           row_number() OVER (PARTITION BY l.tipo_codigo
                                  ORDER BY l.prioridade DESC, l.disponivel_em, l.id) AS ordem_tipo,
           row_number() OVER (PARTITION BY l.chave_bloqueio
                                  ORDER BY l.prioridade DESC, l.disponivel_em, l.id) AS ordem_chave
      FROM livres l
),
escolhidos AS (
    SELECT o.id, t.timeout_segundos
      FROM ordenados o
      JOIN fila_processamento_tipos t ON t.codigo = o.tipo_codigo
      LEFT JOIN LATERAL (
          SELECT count(*) AS ocupados
            FROM fila_processamento oc
            JOIN fila_processamento_status s ON s.id = oc.status_id
           WHERE oc.tipo_codigo = o.tipo_codigo
             AND s.ocupa_worker
      ) uso ON TRUE
     WHERE (o.chave_bloqueio IS NULL OR o.ordem_chave = 1)
       AND (t.concorrencia_maxima IS NULL
            OR uso.ocupados + o.ordem_tipo <= t.concorrencia_maxima)
     ORDER BY o.prioridade DESC, o.disponivel_em, o.id
     LIMIT :lote
)
UPDATE fila_processamento f
   SET status_id = :reservado,
       claim_por = :worker,
       claim_em = now(),
       lease_expira_em = now() + make_interval(secs => e.timeout_segundos + :margem_lease),
       tentativa = f.tentativa + 1,
       iniciado_em = now(),
       atualizado_em = now()
  FROM escolhidos e
 WHERE f.id = e.id
RETURNING f.id, f.tipo_codigo, f.classe, f.destino, f.prioridade, f.disponivel_em, f.origem, f.status_id,
          f.grupo_id, f.ordem_no_grupo, f.correlation_id, f.chave_bloqueio, f.chave_idempotencia,
          f.payload, f.payload_enviado, f.tentativa, f.max_tentativas,
          f.cancelamento_solicitado, f.solicitante_usuario_id, f.lease_expira_em, f.criado_em
"""


def reivindicar(
    conn,
    catalogo: Catalogo,
    classe: str,
    destino: str,
    worker: str,
    lote: int,
    margem_lease: int,
    janela: Optional[int] = None,
) -> list:
    """Claim de até `lote` itens, em um roundtrip. Devolve o que ficou com
    este worker (pode ser menos que `lote`, ou nada)."""
    if lote <= 0:
        return []
    linhas = conn.execute(text(SQL_CLAIM), {
        "pendente": catalogo.id_status(PENDENTE),
        "reservado": catalogo.id_status(RESERVADO),
        "classe": classe,
        "destino": destino,
        "worker": worker,
        "lote": lote,
        "janela": janela if janela and janela >= lote else lote * 4,
        "margem_lease": float(margem_lease),
        "ns_chave": NS_CHAVE_BLOQUEIO,
        "ns_tipo": NS_TIPO,
    }).mappings().all()
    linhas = sorted(
        linhas,
        key=lambda l: (-int(l["prioridade"]), l["disponivel_em"], l["id"]),
    )
    return [_para_item(linha) for linha in linhas]


def _para_item(linha) -> ItemReivindicado:
    return ItemReivindicado(
        id=linha["id"],
        tipo_codigo=linha["tipo_codigo"],
        classe=linha["classe"],
        destino=linha["destino"],
        prioridade=int(linha["prioridade"]),
        origem=linha["origem"],
        status_id=linha["status_id"],
        grupo_id=linha["grupo_id"],
        ordem_no_grupo=linha["ordem_no_grupo"],
        correlation_id=linha["correlation_id"],
        chave_bloqueio=linha["chave_bloqueio"],
        chave_idempotencia=linha["chave_idempotencia"],
        payload=linha["payload"] or {},
        payload_enviado=linha["payload_enviado"],
        tentativa=int(linha["tentativa"]),
        max_tentativas=int(linha["max_tentativas"]),
        cancelamento_solicitado=bool(linha["cancelamento_solicitado"]),
        solicitante_usuario_id=linha["solicitante_usuario_id"],
        lease_expira_em=linha["lease_expira_em"],
        criado_em=linha["criado_em"],
    )


# --- Eventos ----------------------------------------------------------------

def registrar_evento(
    conn,
    item_id: int,
    evento: str,
    ator: str,
    tentativa: Optional[int] = None,
    status_antes_id: Optional[int] = None,
    status_depois_id: Optional[int] = None,
    duracao_ms: Optional[int] = None,
    detalhe: Optional[dict] = None,
) -> None:
    """Uma linha na timeline append-only. Toda transição passa por aqui."""
    conn.execute(
        text("""
            INSERT INTO fila_processamento_eventos
                (fila_item_id, em, evento, ator, tentativa, status_antes_id, status_depois_id,
                 duracao_ms, detalhe)
            VALUES (:item_id, now(), :evento, :ator, :tentativa, :antes, :depois, :duracao, :detalhe)
        """).bindparams(bindparam("detalhe", type_=JSONB)),
        {
            "item_id": item_id,
            "evento": evento,
            "ator": ator,
            "tentativa": tentativa,
            "antes": status_antes_id,
            "depois": status_depois_id,
            "duracao": duracao_ms,
            "detalhe": detalhe,
        },
    )


# --- Transições -------------------------------------------------------------

def _transicionar(conn, item_id: int, de_status: int, para_status: int, extra_sql: str, parametros: dict) -> bool:
    """CAS de status. Zero linhas significa que o reaper (ou um cancelamento)
    chegou antes — o chamador não deve insistir."""
    resultado = conn.execute(text(f"""
        UPDATE fila_processamento
           SET status_id = :para, atualizado_em = now(){extra_sql}
         WHERE id = :id AND status_id = :de
    """), {**parametros, "id": item_id, "de": de_status, "para": para_status})
    return resultado.rowcount == 1


def marcar_executando(conn, catalogo: Catalogo, item: ItemReivindicado, lease_segundos: float, ator: str) -> bool:
    """`reservado -> executando`: a chamada vai sair agora. Daqui para a frente
    o reaper NÃO devolve item não idempotente para `pendente`."""
    reservado = catalogo.id_status(RESERVADO)
    executando = catalogo.id_status(EXECUTANDO)
    ok = _transicionar(
        conn, item.id, reservado, executando,
        ", lease_expira_em = now() + make_interval(secs => :lease)",
        {"lease": float(lease_segundos)},
    )
    if ok:
        registrar_evento(conn, item.id, "executando", ator, item.tentativa, reservado, executando)
    return ok


def renovar_lease(conn, item_id: int, worker: str, lease_segundos: float) -> bool:
    """Renovação enquanto a chamada está em voo. Só renova o que ainda é deste
    worker e ainda está ocupando slot."""
    resultado = conn.execute(text("""
        UPDATE fila_processamento f
           SET lease_expira_em = now() + make_interval(secs => :lease), atualizado_em = now()
          FROM fila_processamento_status s
         WHERE f.id = :id AND f.claim_por = :worker AND s.id = f.status_id AND s.ocupa_worker
    """), {"id": item_id, "worker": worker, "lease": float(lease_segundos)})
    return resultado.rowcount == 1


def gravar_payload_enviado(conn, item_id: int, payload: Optional[dict]) -> None:
    """O corpo real, gravado ANTES da chamada: se a resposta se perder, ainda
    se sabe o que foi mandado."""
    if payload is None:
        return
    conn.execute(
        text("UPDATE fila_processamento SET payload_enviado = :payload, atualizado_em = now() WHERE id = :id")
        .bindparams(bindparam("payload", type_=JSONB)),
        {"payload": payload, "id": item_id},
    )


def concluir(conn, catalogo: Catalogo, item: ItemReivindicado, resultado: Optional[dict],
             duracao_ms: Optional[int], ator: str, de_status: Optional[int] = None) -> bool:
    de = de_status if de_status is not None else catalogo.id_status(EXECUTANDO)
    para = catalogo.id_status(CONCLUIDO)
    ok = conn.execute(
        text("""
            UPDATE fila_processamento
               SET status_id = :para, resultado = :resultado, erro = NULL, erro_codigo = NULL,
                   finalizado_em = now(), duracao_ms = :duracao, lease_expira_em = NULL,
                   atualizado_em = now()
             WHERE id = :id AND status_id = :de
        """).bindparams(bindparam("resultado", type_=JSONB)),
        {"id": item.id, "de": de, "para": para, "resultado": resultado, "duracao": duracao_ms},
    ).rowcount == 1
    if ok:
        registrar_evento(conn, item.id, "concluido", ator, item.tentativa, de, para, duracao_ms,
                         {"cancelamento_solicitado": item.cancelamento_solicitado} if item.cancelamento_solicitado else None)
    return ok


def falhar(conn, catalogo: Catalogo, item: ItemReivindicado, erro: str, erro_codigo: Optional[str],
           duracao_ms: Optional[int], ator: str, de_status: Optional[int] = None) -> bool:
    """Falha DEFINITIVA: o destino processou e recusou. Vai direto a terminal,
    sem consumir o orçamento de tentativas em esperas inúteis — o CNPJ inválido
    não fica melhor na terceira vez (seção 5 do plano)."""
    de = de_status if de_status is not None else catalogo.id_status(EXECUTANDO)
    para = catalogo.id_status(FALHOU)
    ok = _transicionar(
        conn, item.id, de, para,
        """, erro = :erro, erro_codigo = :erro_codigo, finalizado_em = now(),
             duracao_ms = :duracao, lease_expira_em = NULL""",
        {"erro": erro, "erro_codigo": erro_codigo, "duracao": duracao_ms},
    )
    if ok:
        registrar_evento(conn, item.id, "falhou", ator, item.tentativa, de, para, duracao_ms,
                         {"erro_codigo": erro_codigo} if erro_codigo else None)
    return ok


def agendar_retentativa(conn, catalogo: Catalogo, item: ItemReivindicado, erro: str,
                        erro_codigo: Optional[str], atraso_segundos: float, ator: str,
                        de_status: Optional[int] = None) -> str:
    """Devolve a linha para `pendente` com `disponivel_em` no futuro.

    **Nenhuma thread dorme esperando o destino**: a espera vira dado na linha e
    o worker segue com o próximo item. Sem orçamento de tentativas sobrando, o
    item vai para a DLQ em vez de voltar e nunca mais ser reivindicado (o claim
    exige `tentativa < max_tentativas`)."""
    de = de_status if de_status is not None else catalogo.id_status(EXECUTANDO)
    if item.ultima_tentativa:
        if enviar_para_dlq(conn, catalogo, item, f"tentativas esgotadas: {erro}", ator, de_status=de):
            return DLQ
        return ""

    para = catalogo.id_status(PENDENTE)
    ok = _transicionar(
        conn, item.id, de, para,
        """, erro = :erro, erro_codigo = :erro_codigo, lease_expira_em = NULL,
             claim_por = NULL, claim_em = NULL,
             disponivel_em = now() + make_interval(secs => :atraso)""",
        {"erro": erro, "erro_codigo": erro_codigo, "atraso": float(atraso_segundos)},
    )
    if ok:
        registrar_evento(conn, item.id, "retentativa_agendada", ator, item.tentativa, de, para, None,
                         {"atraso_segundos": round(atraso_segundos, 3), "erro_codigo": erro_codigo})
        return PENDENTE
    return ""


def marcar_incerto(conn, catalogo: Catalogo, item: ItemReivindicado, erro: str,
                   erro_codigo: Optional[str], ator: str, de_status: Optional[int] = None) -> bool:
    """O destino PODE ter processado. `incerto` não é terminal e não ocupa
    worker: sai da mão do motor e entra na verificação automática (quando o
    tipo tem `tipo_verificacao_codigo`) ou na decisão humana."""
    de = de_status if de_status is not None else catalogo.id_status(EXECUTANDO)
    para = catalogo.id_status(INCERTO)
    ok = _transicionar(
        conn, item.id, de, para,
        ", erro = :erro, erro_codigo = :erro_codigo, lease_expira_em = NULL",
        {"erro": erro, "erro_codigo": erro_codigo},
    )
    if ok:
        registrar_evento(conn, item.id, "incerto", ator, item.tentativa, de, para, None,
                         {"erro_codigo": erro_codigo} if erro_codigo else None)
    return ok


def marcar_aguardando_callback(conn, catalogo: Catalogo, item: ItemReivindicado,
                               resultado: Optional[dict], ator: str,
                               de_status: Optional[int] = None) -> bool:
    de = de_status if de_status is not None else catalogo.id_status(EXECUTANDO)
    para = catalogo.id_status(AGUARDANDO_CALLBACK)
    ok = conn.execute(
        text("""
            UPDATE fila_processamento
               SET status_id = :para, resultado = :resultado, lease_expira_em = NULL, atualizado_em = now()
             WHERE id = :id AND status_id = :de
        """).bindparams(bindparam("resultado", type_=JSONB)),
        {"id": item.id, "de": de, "para": para, "resultado": resultado},
    ).rowcount == 1
    if ok:
        registrar_evento(conn, item.id, "aguardando_callback", ator, item.tentativa, de, para)
    return ok


def enviar_para_dlq(conn, catalogo: Catalogo, item: ItemReivindicado, motivo: str, ator: str,
                    de_status: Optional[int] = None) -> bool:
    """DLQ é estado da própria linha, não outro lugar."""
    para = catalogo.id_status(DLQ)
    filtro = "AND status_id = :de" if de_status is not None else ""
    parametros = {"id": item.id, "para": para, "motivo": motivo}
    if de_status is not None:
        parametros["de"] = de_status
    ok = conn.execute(text(f"""
        UPDATE fila_processamento
           SET status_id = :para, dlq_em = now(), dlq_motivo = :motivo,
               finalizado_em = COALESCE(finalizado_em, now()), lease_expira_em = NULL,
               atualizado_em = now()
         WHERE id = :id {filtro}
    """), parametros).rowcount == 1
    if ok:
        registrar_evento(conn, item.id, "dlq", ator, item.tentativa, de_status, para, None, {"motivo": motivo})
    return ok


def liberar_sem_handler(conn, catalogo: Catalogo, item: ItemReivindicado, atraso_segundos: float,
                        ator: str) -> bool:
    """Tipo ativo no banco sem handler neste worker.

    Devolve a linha a `pendente` DEVOLVENDO a tentativa consumida: o item não
    fez nada errado, e outra instância (ou o deploy que traz o handler) ainda
    pode pegá-lo. Sem isso, um tipo ativado antes do deploy iria para a DLQ
    sozinho."""
    reservado = catalogo.id_status(RESERVADO)
    pendente = catalogo.id_status(PENDENTE)
    ok = _transicionar(
        conn, item.id, reservado, pendente,
        """, tentativa = GREATEST(0, tentativa - 1), claim_por = NULL, claim_em = NULL,
             lease_expira_em = NULL, disponivel_em = now() + make_interval(secs => :atraso)""",
        {"atraso": float(atraso_segundos)},
    )
    if ok:
        registrar_evento(conn, item.id, "sem_handler", ator, item.tentativa, reservado, pendente, None,
                         {"tipo_codigo": item.tipo_codigo})
    return ok


# --- Reaper -----------------------------------------------------------------

def reaper(conn, catalogo: Catalogo, ator: str, envelhecimento_minutos: int,
           envelhecimento_passo: int, envelhecimento_teto: int, limite: int = 200) -> dict:
    """Varre leases vencidos e envelhece a fila assíncrona.

    Sob `pg_try_advisory_xact_lock` — se outra instância está varrendo, esta
    pula o ciclo (30 s depois tenta de novo). `try_` porque lock de sessão não
    existe no pooler e um lock bloqueante prenderia a thread do agendador.

    A regra crítica: item **não idempotente** cujo lease venceu em `executando`
    NUNCA volta para `pendente` — o destino pode ter processado. Vai para
    `incerto`. Só volta a `pendente` o que venceu ainda em `reservado` (a
    chamada não chegou a sair) ou o que é de tipo idempotente.
    """
    if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:ns, 0)"), {"ns": NS_REAPER}).scalar():
        return {"ignorado": True, "devolvidos": 0, "incertos": 0, "dlq": 0, "envelhecidos": 0}

    pendente = catalogo.id_status(PENDENTE)
    reservado = catalogo.id_status(RESERVADO)
    executando = catalogo.id_status(EXECUTANDO)
    incerto = catalogo.id_status(INCERTO)
    dlq = catalogo.id_status(DLQ)

    # 1. Lease vencido que ainda pode voltar: `reservado` (a chamada não saiu)
    #    ou tipo idempotente (repetir é seguro). Só volta quem tem tentativa
    #    sobrando; quem esgotou vai para a DLQ no passo 3.
    devolvidos = conn.execute(text("""
        WITH alvo AS (
            SELECT f.id, f.status_id, f.tentativa
              FROM fila_processamento f
              JOIN fila_processamento_tipos t ON t.codigo = f.tipo_codigo
             WHERE f.status_id IN (:reservado, :executando)
               AND f.lease_expira_em <= now()
               AND (f.status_id = :reservado OR t.idempotente)
               AND f.tentativa < f.max_tentativas
             ORDER BY f.lease_expira_em
             LIMIT :limite
             FOR UPDATE OF f SKIP LOCKED
        )
        UPDATE fila_processamento f
           SET status_id = :pendente, claim_por = NULL, claim_em = NULL, lease_expira_em = NULL,
               disponivel_em = now(), erro = COALESCE(f.erro, 'lease expirado'),
               erro_codigo = 'LEASE_EXPIRADO', atualizado_em = now()
          FROM alvo a
         WHERE f.id = a.id
        RETURNING f.id, a.status_id AS antes, f.tentativa
    """), {"reservado": reservado, "executando": executando, "pendente": pendente, "limite": limite}).mappings().all()
    for linha in devolvidos:
        registrar_evento(conn, linha["id"], "lease_expirado_devolvido", ator, linha["tentativa"],
                         linha["antes"], pendente)

    # 2. Lease vencido em `executando` de tipo NÃO idempotente: `incerto`.
    incertos = conn.execute(text("""
        WITH alvo AS (
            SELECT f.id, f.tentativa
              FROM fila_processamento f
              JOIN fila_processamento_tipos t ON t.codigo = f.tipo_codigo
             WHERE f.status_id = :executando
               AND f.lease_expira_em <= now()
               AND NOT t.idempotente
             ORDER BY f.lease_expira_em
             LIMIT :limite
             FOR UPDATE OF f SKIP LOCKED
        )
        UPDATE fila_processamento f
           SET status_id = :incerto, lease_expira_em = NULL,
               erro = 'Lease expirado com a chamada em voo: o destino pode ter processado.',
               erro_codigo = 'LEASE_EXPIRADO_EM_VOO', atualizado_em = now()
          FROM alvo a
         WHERE f.id = a.id
        RETURNING f.id, f.tentativa
    """), {"executando": executando, "incerto": incerto, "limite": limite}).mappings().all()
    for linha in incertos:
        registrar_evento(conn, linha["id"], "lease_expirado_incerto", ator, linha["tentativa"],
                         executando, incerto)

    # 3. Lease vencido, retentável, mas sem tentativa sobrando: DLQ. Sem isso a
    #    linha voltaria a `pendente` e nunca mais seria reivindicada.
    esgotados = conn.execute(text("""
        WITH alvo AS (
            SELECT f.id, f.status_id, f.tentativa
              FROM fila_processamento f
              JOIN fila_processamento_tipos t ON t.codigo = f.tipo_codigo
             WHERE f.status_id IN (:reservado, :executando)
               AND f.lease_expira_em <= now()
               AND (f.status_id = :reservado OR t.idempotente)
               AND f.tentativa >= f.max_tentativas
             ORDER BY f.lease_expira_em
             LIMIT :limite
             FOR UPDATE OF f SKIP LOCKED
        )
        UPDATE fila_processamento f
           SET status_id = :dlq, dlq_em = now(),
               dlq_motivo = 'lease expirado com as tentativas esgotadas',
               finalizado_em = COALESCE(f.finalizado_em, now()), lease_expira_em = NULL,
               atualizado_em = now()
          FROM alvo a
         WHERE f.id = a.id
        RETURNING f.id, a.status_id AS antes, f.tentativa
    """), {"reservado": reservado, "executando": executando, "dlq": dlq, "limite": limite}).mappings().all()
    for linha in esgotados:
        registrar_evento(conn, linha["id"], "dlq", ator, linha["tentativa"], linha["antes"], dlq, None,
                         {"motivo": "lease expirado com as tentativas esgotadas"})

    # 4. Envelhecimento (seção 3 do plano): +passo a cada `envelhecimento_minutos`
    #    na fila assíncrona, com teto para não invadir a faixa interativa.
    #    `atualizado_em` é o relógio do passo — o próprio UPDATE o reposiciona,
    #    então o reaper de 30 s não sobe a prioridade a cada volta.
    envelhecidos = conn.execute(text("""
        WITH alvo AS (
            SELECT f.id, f.prioridade, f.tentativa
              FROM fila_processamento f
             WHERE f.status_id = :pendente
               AND f.classe = 'assincrono'
               AND f.prioridade < :teto
               AND f.disponivel_em <= now()
               AND f.atualizado_em <= now() - make_interval(mins => :minutos)
             ORDER BY f.atualizado_em
             LIMIT :limite
             FOR UPDATE OF f SKIP LOCKED
        )
        UPDATE fila_processamento f
           SET prioridade = LEAST(:teto, f.prioridade + :passo), atualizado_em = now()
          FROM alvo a
         WHERE f.id = a.id
        RETURNING f.id, a.prioridade AS antes, f.prioridade AS depois, f.tentativa
    """), {
        "pendente": pendente, "teto": envelhecimento_teto, "passo": envelhecimento_passo,
        "minutos": envelhecimento_minutos, "limite": limite,
    }).mappings().all()
    for linha in envelhecidos:
        registrar_evento(conn, linha["id"], "envelhecido", ator, linha["tentativa"], None, None, None,
                         {"prioridade_antes": linha["antes"], "prioridade_depois": linha["depois"]})

    return {
        "ignorado": False,
        "devolvidos": len(devolvidos),
        "incertos": len(incertos),
        "dlq": len(esgotados),
        "envelhecidos": len(envelhecidos),
    }


# --- Heartbeat --------------------------------------------------------------

def identificador_padrao(pid: int) -> str:
    return f"{socket.gethostname()}:{pid}"


def heartbeat(conn, identificador: str, classes: list, versao: Optional[str], pid: int,
              capacidade: int, itens_em_execucao: int, detalhe: Optional[dict] = None) -> None:
    """Upsert em `fila_workers`. `vw_fila_workers` marca como não saudável quem
    passa de 2 min sem passar por aqui."""
    conn.execute(
        text("""
            INSERT INTO fila_workers
                (identificador, host, classes, versao, pid, capacidade, itens_em_execucao,
                 iniciado_em, visto_em, detalhe)
            VALUES (:identificador, :host, :classes, :versao, :pid, :capacidade, :itens,
                    now(), now(), :detalhe)
            ON CONFLICT (identificador) DO UPDATE
               SET host = EXCLUDED.host, classes = EXCLUDED.classes, versao = EXCLUDED.versao,
                   pid = EXCLUDED.pid, capacidade = EXCLUDED.capacidade,
                   itens_em_execucao = EXCLUDED.itens_em_execucao, visto_em = now(),
                   detalhe = EXCLUDED.detalhe
        """).bindparams(bindparam("detalhe", type_=JSONB)),
        {
            "identificador": identificador,
            "host": socket.gethostname(),
            "classes": classes,
            "versao": versao,
            "pid": pid,
            "capacidade": capacidade,
            "itens": itens_em_execucao,
            "detalhe": detalhe,
        },
    )
