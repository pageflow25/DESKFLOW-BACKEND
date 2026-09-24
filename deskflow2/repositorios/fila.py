"""Leitura e claim da fila do PCP direto no banco.

Contrato (BACKEND_PAGEFLOW/docs/pcp/modulo-pcp.md, seção 12): o consumidor só
busca pendências, faz o claim atômico (compare-and-set) e grava o que é dele
— modo, data e corpo do envio, e o id_requisicao do ack assíncrono. Todo o
resultado (resposta, erro, status final, pedidos, histórico) é gravado pelo
PageFlow, a partir do que chega nos endpoints de retorno.
"""

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from .status import (
    AGUARDANDO_RETORNO,
    EM_FILA,
    EM_PROCESSAMENTO,
    PENDENTE_ENVIO,
    SUCESSO,
    CatalogoStatus,
)

TABELA_ORCAMENTOS = "orcamento_api_orcamentos"
TABELA_APROVACOES = "orcamento_api_aprovacoes"
TABELAS_CHAMADA = (TABELA_ORCAMENTOS, TABELA_APROVACOES)


ORIGEM_ESCOLA = "escola"
ORIGEM_INTEGRACAO = "integracao"


@dataclass
class OrcamentoReivindicado:
    """Um orçamento da fila do PCP, nas DUAS origens de lote.

    `origem` (orcamento_api_lotes.origem) diz de onde vêm os itens, e as duas
    listas são EXCLUSIVAS — o CHECK `ck_orcamento_api_itens_origem` garante
    isso no banco:
      - 'escola'     -> `pedido_distribuicao_ids`, com `modo_agrupamento`;
      - 'integracao' -> `integra_pedido_produto_ids`, sem modo (a divisão é
        fixa: 1 orçamento por pedido do parceiro).

    Use `ids_origem` para não ramificar por origem em cada chamador.
    """

    id: int
    requisicao_id: int
    lote_id: int
    modo_envio: Optional[str]
    url_webhook: Optional[str]
    modo_agrupamento: Optional[str]
    cliente_id: Optional[int]
    vendedor_id: Optional[int]
    forma_pagamento: Optional[int]
    pedido_distribuicao_ids: list
    origem: str = ORIGEM_ESCOLA
    integra_pedido_produto_ids: list = field(default_factory=list)
    # Só na origem integração: a data de entrega que o usuário escolheu no
    # "Enviar", já como 'DD/MM/YYYY', para entrar no `obs_producao` do item
    # (sql/orcamento_integracao.sql). Não é a data que vai ao ERP como data do
    # item — essa é a `data_saida`, que o PageFlow manda na APROVAÇÃO.
    data_entrega: Optional[str] = None

    @property
    def ids_origem(self) -> list:
        """Ids que o `codigo_externo` dos itens tem de reproduzir."""
        if self.origem == ORIGEM_INTEGRACAO:
            return self.integra_pedido_produto_ids
        return self.pedido_distribuicao_ids


@dataclass
class AprovacaoReivindicada:
    id: int
    orcamento_id: int
    id_orcamento: Optional[int]
    gerar_op: bool
    itens_aprovados: list
    modo_envio: Optional[str]
    url_webhook: Optional[str]


# --- Orçamentos -----------------------------------------------------------------

def listar_orcamentos_pendentes(conn, status: CatalogoStatus, limite: int) -> list:
    return list(conn.execute(text("""
        SELECT o.id
          FROM orcamento_api_orcamentos o
         WHERE o.status_id = :pendente
           AND o.substituida_por_id IS NULL
         ORDER BY o.criado_em, o.id
         LIMIT :limite
    """), {"pendente": status.id_chamada(PENDENTE_ENVIO), "limite": limite}).scalars())


def _modo_efetivo(alias: str = "") -> str:
    """Modo gravado no claim. Vale o `modo_envio` que já está na linha; vazio,
    usa o padrão (`:modo`, de PCP_MODO_ENVIO). Sem url_webhook
    (BACKEND_PUBLIC_BASE_URL não configurada no PageFlow) não há para onde o
    ERP devolver o resultado: a linha vai no modo síncrono."""
    return (
        f"CASE WHEN {alias}url_webhook IS NULL THEN 'sincrono' "
        f"ELSE COALESCE({alias}modo_envio::text, CAST(:modo AS text)) END"
    )


# Aprovação de orçamento que foi assíncrono vai assíncrona também, sem olhar a
# url_webhook da aprovação. `modo_envio` já gravado na aprovação continua valendo.
_MODO_EFETIVO_APROVACAO = (
    "CASE WHEN a.modo_envio IS NULL AND o.modo_envio::text = 'assincrono' THEN 'assincrono' "
    f"ELSE {_modo_efetivo('a.')} END"
)


def reivindicar_orcamento(conn, status: CatalogoStatus, orcamento_id: int, modo_padrao: str) -> Optional[OrcamentoReivindicado]:
    """Claim CAS `pendente_envio -> aguardando_retorno`; o lote vai de
    `em_fila` para `em_processamento` na mesma transação. Sem linha
    atualizada, outro processo pegou (ou a linha mudou): não chamar o ERP.
    `modo_padrao` só vale para linha sem `modo_envio`."""
    linha = conn.execute(text(f"""
        UPDATE orcamento_api_orcamentos
           SET status_id = :aguardando,
               modo_envio = CAST({_modo_efetivo()} AS enum_orcamento_api_orcamentos_modo_envio),
               data_envio = NOW(),
               atualizado_em = NOW()
         WHERE id = :id
           AND status_id = :pendente
           AND substituida_por_id IS NULL
        RETURNING id
    """), {
        "aguardando": status.id_chamada(AGUARDANDO_RETORNO),
        "pendente": status.id_chamada(PENDENTE_ENVIO),
        "modo": modo_padrao,
        "id": orcamento_id,
    }).first()
    if not linha:
        return None

    orcamento = carregar_orcamento(conn, orcamento_id)
    conn.execute(text("""
        UPDATE orcamento_api_lotes
           SET status_id = :em_processamento, atualizado_em = NOW()
         WHERE id = :lote_id AND status_id = :em_fila
    """), {
        "em_processamento": status.id_lote(EM_PROCESSAMENTO),
        "em_fila": status.id_lote(EM_FILA),
        "lote_id": orcamento.lote_id,
    })
    return orcamento


def carregar_orcamento(conn, orcamento_id: int) -> Optional[OrcamentoReivindicado]:
    """Dados para montar a chamada: grupo (cliente/vendedor/forma), modo do
    lote e os pedidos do orçamento. Também usado pelo dry-run, sem claim."""
    linha = conn.execute(text("""
        SELECT o.id, o.orcamento_api_requisicao_id, o.modo_envio::text AS modo_envio, o.url_webhook,
               r.orcamento_api_lote_id AS lote_id, r.cliente_id, r.vendedor_id, r.forma_pagamento,
               p.origem::text AS origem,
               p.modo_agrupamento::text AS modo_agrupamento,
               to_char(o.data_entrega, 'DD/MM/YYYY') AS data_entrega
          FROM orcamento_api_orcamentos o
          JOIN orcamento_api_requisicoes r ON r.id = o.orcamento_api_requisicao_id
          JOIN orcamento_api_lotes p ON p.id = r.orcamento_api_lote_id
         WHERE o.id = :id
    """), {"id": orcamento_id}).mappings().first()
    if not linha:
        return None

    # Uma consulta só para as duas origens: o item tem exatamente uma das duas
    # colunas preenchida, então cada lista sai com os ids que lhe cabem.
    itens = conn.execute(text("""
        SELECT pedido_distribuicao_id, integra_pedido_produto_id
          FROM orcamento_api_itens
         WHERE orcamento_api_orcamento_id = :id
         ORDER BY pedido_distribuicao_id NULLS LAST, integra_pedido_produto_id NULLS LAST
    """), {"id": orcamento_id}).mappings().all()

    pedidos = [item["pedido_distribuicao_id"] for item in itens if item["pedido_distribuicao_id"] is not None]
    produtos = [item["integra_pedido_produto_id"] for item in itens if item["integra_pedido_produto_id"] is not None]

    return OrcamentoReivindicado(
        id=linha["id"],
        requisicao_id=linha["orcamento_api_requisicao_id"],
        lote_id=linha["lote_id"],
        modo_envio=linha["modo_envio"],
        url_webhook=linha["url_webhook"],
        modo_agrupamento=linha["modo_agrupamento"],
        cliente_id=linha["cliente_id"],
        vendedor_id=linha["vendedor_id"],
        forma_pagamento=linha["forma_pagamento"],
        pedido_distribuicao_ids=pedidos,
        origem=linha["origem"] or ORIGEM_ESCOLA,
        integra_pedido_produto_ids=produtos,
        data_entrega=linha["data_entrega"],
    )


# --- Aprovações ---------------------------------------------------------------

def listar_aprovacoes_pendentes(conn, status: CatalogoStatus, limite: int) -> list:
    return list(conn.execute(text("""
        SELECT a.id
          FROM orcamento_api_aprovacoes a
         WHERE a.status_id = :pendente
           AND a.substituida_por_id IS NULL
         ORDER BY a.criado_em, a.id
         LIMIT :limite
    """), {"pendente": status.id_chamada(PENDENTE_ENVIO), "limite": limite}).scalars())


def reivindicar_aprovacao(conn, status: CatalogoStatus, aprovacao_id: int, modo_padrao: str) -> Optional[AprovacaoReivindicada]:
    """Mesmo claim do orçamento. O modo segue o do orçamento quando ele foi
    assíncrono; `modo_padrao` só vale para linha sem modo definido."""
    linha = conn.execute(text(f"""
        UPDATE orcamento_api_aprovacoes a
           SET status_id = :aguardando,
               modo_envio = CAST({_MODO_EFETIVO_APROVACAO} AS enum_orcamento_api_aprovacoes_modo_envio),
               data_envio = NOW(),
               atualizado_em = NOW()
          FROM orcamento_api_orcamentos o
         WHERE a.id = :id
           AND a.status_id = :pendente
           AND a.substituida_por_id IS NULL
           AND o.id = a.orcamento_api_orcamento_id
        RETURNING a.id, a.orcamento_api_orcamento_id, o.id_orcamento, a.gerar_op, a.itens_aprovados,
                  a.modo_envio::text AS modo_envio, a.url_webhook
    """), {
        "aguardando": status.id_chamada(AGUARDANDO_RETORNO),
        "pendente": status.id_chamada(PENDENTE_ENVIO),
        "modo": modo_padrao,
        "id": aprovacao_id,
    }).mappings().first()
    if not linha:
        return None
    return AprovacaoReivindicada(
        id=linha["id"],
        orcamento_id=linha["orcamento_api_orcamento_id"],
        id_orcamento=linha["id_orcamento"],
        gerar_op=bool(linha["gerar_op"]),
        itens_aprovados=linha["itens_aprovados"] or [],
        modo_envio=linha["modo_envio"],
        url_webhook=linha["url_webhook"],
    )


def id_orcamento_da_aprovacao(conn, aprovacao_id: int) -> Optional[int]:
    return conn.execute(text("""
        SELECT o.id_orcamento
          FROM orcamento_api_aprovacoes a
          JOIN orcamento_api_orcamentos o ON o.id = a.orcamento_api_orcamento_id
         WHERE a.id = :id
    """), {"id": aprovacao_id}).scalar()


# --- Gravações do consumidor -----------------------------------------------------

def gravar_payload_enviado(conn, tabela: str, registro_id: int, payload: dict) -> None:
    _validar_tabela(tabela)
    conn.execute(
        text(f"UPDATE {tabela} SET payload_enviado = :payload, atualizado_em = NOW() WHERE id = :id")
        .bindparams(bindparam("payload", type_=JSONB)),
        {"payload": payload, "id": registro_id},
    )


def gravar_id_requisicao(conn, status: CatalogoStatus, tabela: str, registro_id: int, id_requisicao: int) -> bool:
    """Grava o id_requisicao do ack assíncrono — só enquanto a linha ainda
    espera retorno (o webhook pode ter chegado antes do ack ser gravado)."""
    _validar_tabela(tabela)
    resultado = conn.execute(text(f"""
        UPDATE {tabela}
           SET id_requisicao = :id_requisicao, atualizado_em = NOW()
         WHERE id = :id AND status_id = :aguardando AND id_requisicao IS NULL
    """), {"id_requisicao": id_requisicao, "id": registro_id, "aguardando": status.id_chamada(AGUARDANDO_RETORNO)})
    return resultado.rowcount == 1


# --- Reconciliação -----------------------------------------------------------------

def listar_aguardando_retorno(conn, status: CatalogoStatus, tabela: str, minutos: int, limite: int = 100) -> list:
    _validar_tabela(tabela)
    return [dict(linha) for linha in conn.execute(text(f"""
        SELECT id, id_requisicao, data_envio,
               EXTRACT(EPOCH FROM (NOW() - data_envio)) / 3600.0 AS horas_esperando
          FROM {tabela}
         WHERE status_id = :aguardando
           AND substituida_por_id IS NULL
           AND data_envio < NOW() - make_interval(mins => :minutos)
         ORDER BY data_envio
         LIMIT :limite
    """), {"aguardando": status.id_chamada(AGUARDANDO_RETORNO), "minutos": minutos, "limite": limite}).mappings()]


# --- Downloads dos arquivos da OP --------------------------------------------------

def listar_downloads_pendentes(conn, status: CatalogoStatus, reinicio_minutos: int, limite: int) -> list:
    return list(conn.execute(text("""
        SELECT a.id
          FROM orcamento_api_aprovacoes a
          JOIN orcamento_api_orcamentos o ON o.id = a.orcamento_api_orcamento_id
          JOIN orcamento_api_requisicoes r ON r.id = o.orcamento_api_requisicao_id
          JOIN orcamento_api_lotes p ON p.id = r.orcamento_api_lote_id
         WHERE p.baixar_arquivos
           AND a.gerar_op
           AND a.status_id = :sucesso
           AND a.substituida_por_id IS NULL
           AND a.downloads_em IS NULL
           AND (a.downloads_iniciado_em IS NULL
                OR a.downloads_iniciado_em < NOW() - make_interval(mins => :reinicio))
         ORDER BY a.data_retorno, a.id
         LIMIT :limite
    """), {"sucesso": status.id_chamada(SUCESSO), "reinicio": reinicio_minutos, "limite": limite}).scalars())


def reivindicar_download(conn, aprovacao_id: int, reinicio_minutos: int) -> bool:
    """Claim do download: marca o início. Um claim parado há mais de
    `reinicio_minutos` (processo morreu no meio) pode ser retomado — baixar de
    novo é seguro, a pasta é montada à parte e só publicada no fim."""
    resultado = conn.execute(text("""
        UPDATE orcamento_api_aprovacoes
           SET downloads_iniciado_em = NOW(), atualizado_em = NOW()
         WHERE id = :id
           AND downloads_em IS NULL
           AND (downloads_iniciado_em IS NULL
                OR downloads_iniciado_em < NOW() - make_interval(mins => :reinicio))
    """), {"id": aprovacao_id, "reinicio": reinicio_minutos})
    return resultado.rowcount == 1


def arquivos_da_aprovacao(conn, aprovacao_id: int) -> list:
    """Arquivos de cada OP da aprovação, nas DUAS origens de lote.

    - Lote de ESCOLA: OP (orcamento_api_itens_retorno.id_op) -> pedido ->
      `pedido_distribuicao_arquivos` -> `pedido_arquivos_pdf` (Vercel Blob).
      Uma linha por pedido x arquivo; `arquivo_pdf_id` identifica o arquivo.
    - Lote de INTEGRAÇÃO: OP -> produto do pedido do parceiro
      (`integra_pedido_produtos`), cujos arquivos são URLs na própria linha:
      `arquivo_pdf` e, quando existirem, `design_capa_frente`/`design_capa_verso`.
      Não há `pedido_arquivos_pdf`, então `arquivo_pdf_id` vem NULL e a
      identidade do arquivo é (produto, tipo_arquivo). `mockup_capa_frente`,
      `mockup_capa_costas` e `etiqueta_produto` NÃO são baixados: são material
      de conferência/expedição, não arquivo de produção da OP.

    As duas metades devolvem as mesmas chaves, e `pasta` é o nome do primeiro
    nível da pasta de destino: a escola, ou "integração/numero_pedido".
    """
    return [dict(linha) for linha in conn.execute(text("""
        WITH aprovacao AS (
            SELECT p.origem::text AS origem, e.nome AS escola_nome, i.nome AS integracao_nome
              FROM orcamento_api_aprovacoes a
              JOIN orcamento_api_orcamentos o ON o.id = a.orcamento_api_orcamento_id
              JOIN orcamento_api_requisicoes r ON r.id = o.orcamento_api_requisicao_id
              JOIN orcamento_api_lotes p ON p.id = r.orcamento_api_lote_id
              LEFT JOIN escola_escolas e ON e.id = p.escola_id
              LEFT JOIN integra_integracoes_externas i ON i.id = p.integracao_id
             WHERE a.id = :aprovacao_id
        )
        -- Origem escola
        SELECT ir.id_op,
               ir.pedido_distribuicao_id,
               NULL::int AS integra_pedido_produto_id,
               pda.arquivo_pdf_id,
               ap.nome AS arquivo_nome,
               COALESCE(NULLIF(ap.caminho_remoto, ''), ap.arquivo) AS url,
               ap.tipo_arquivo,
               -- Identidade do arquivo para deduplicar o download: o ID, nunca
               -- o nome. Dois PDFs diferentes podem ter o mesmo nome, e
               -- deduplicar por nome perderia um deles.
               pda.arquivo_pdf_id::text AS chave_arquivo,
               COALESCE(ac.escola_nome, 'Escola sem nome') AS pasta,
               1 AS ordem_origem
          FROM orcamento_api_itens_retorno ir
          CROSS JOIN aprovacao ac
          JOIN pedido_distribuicao_arquivos pda ON pda.distribuicao_material_id = ir.pedido_distribuicao_id
          JOIN pedido_arquivos_pdf ap ON ap.id = pda.arquivo_pdf_id
         WHERE ir.orcamento_api_aprovacao_id = :aprovacao_id
           AND ir.id_op IS NOT NULL
           AND ir.pedido_distribuicao_id IS NOT NULL

        UNION ALL

        -- Origem integração: os arquivos são URLs no próprio produto.
        SELECT ir.id_op,
               NULL::int AS pedido_distribuicao_id,
               ir.integra_pedido_produto_id,
               NULL::int AS arquivo_pdf_id,
               arq.nome AS arquivo_nome,
               arq.url,
               arq.tipo_arquivo,
               ir.integra_pedido_produto_id::text || ':' || arq.tipo_arquivo AS chave_arquivo,
               COALESCE(ac.integracao_nome, 'Integracao') || ' - ' || COALESCE(ip.numero_pedido, 'sem numero') AS pasta,
               2 AS ordem_origem
          FROM orcamento_api_itens_retorno ir
          CROSS JOIN aprovacao ac
          JOIN integra_pedido_produtos ipp ON ipp.id = ir.integra_pedido_produto_id
          JOIN integra_pedidos ip ON ip.id = ipp.pedido_id
          CROSS JOIN LATERAL (
              VALUES
                  (ipp.arquivo_pdf, 'miolo', ipp.id::text || '_miolo.pdf'),
                  (ipp.design_capa_frente, 'capa_frente', ipp.id::text || '_capa_frente.pdf'),
                  (ipp.design_capa_verso, 'capa_verso', ipp.id::text || '_capa_verso.pdf')
          ) AS arq(url, tipo_arquivo, nome)
         WHERE ir.orcamento_api_aprovacao_id = :aprovacao_id
           AND ir.id_op IS NOT NULL
           AND ir.integra_pedido_produto_id IS NOT NULL
           AND COALESCE(arq.url, '') <> ''

        ORDER BY id_op, ordem_origem, chave_arquivo, pedido_distribuicao_id, integra_pedido_produto_id
    """), {"aprovacao_id": aprovacao_id}).mappings()]


def registrar_downloads_bremen(conn, linhas: list) -> None:
    """Uma linha por origem x arquivo baixado (decisão do usuário: o DeskFlow
    continua gravando downloads_bremen direto). Repetir o download (depois de
    uma falha parcial) não duplica as linhas já gravadas.

    A origem é exclusiva (CHECK `ck_downloads_bremen_origem`): lote de escola
    grava `distribuicao_material_id`, lote de integração grava
    `integra_pedido_produto_id`. Num lote de integração `arquivo_pdf_id` é
    NULL (não existe linha em pedido_arquivos_pdf), por isso a checagem de
    duplicata compara as colunas com `IS NOT DISTINCT FROM` — `= NULL` nunca
    seria verdadeiro e o INSERT duplicaria a cada nova tentativa.
    """
    if not linhas:
        return
    conn.execute(text("""
        INSERT INTO downloads_bremen
            (distribuicao_material_id, integra_pedido_produto_id, id_ops, arquivo_pdf_id,
             tipo_arquivo, caminho_local, tamanho, criado_em)
        SELECT :distribuicao_material_id, :integra_pedido_produto_id, :id_ops, :arquivo_pdf_id,
               :tipo_arquivo, :caminho_local, :tamanho, NOW()
         WHERE NOT EXISTS (
            SELECT 1 FROM downloads_bremen
             WHERE distribuicao_material_id IS NOT DISTINCT FROM :distribuicao_material_id
               AND integra_pedido_produto_id IS NOT DISTINCT FROM :integra_pedido_produto_id
               AND id_ops = :id_ops
               AND arquivo_pdf_id IS NOT DISTINCT FROM :arquivo_pdf_id
               AND caminho_local = :caminho_local
         )
    """), linhas)


def _validar_tabela(tabela: str) -> None:
    if tabela not in TABELAS_CHAMADA:
        raise ValueError(f"Tabela fora da fila do PCP: {tabela}")


__all__ = [
    "TABELA_ORCAMENTOS",
    "TABELA_APROVACOES",
    "OrcamentoReivindicado",
    "AprovacaoReivindicada",
    "listar_orcamentos_pendentes",
    "reivindicar_orcamento",
    "carregar_orcamento",
    "listar_aprovacoes_pendentes",
    "reivindicar_aprovacao",
    "id_orcamento_da_aprovacao",
    "gravar_payload_enviado",
    "gravar_id_requisicao",
    "listar_aguardando_retorno",
    "listar_downloads_pendentes",
    "reivindicar_download",
    "arquivos_da_aprovacao",
    "registrar_downloads_bremen",
]
