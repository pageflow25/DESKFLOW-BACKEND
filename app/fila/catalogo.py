"""Cache dos catálogos da fila: status e tipos.

Os ids de `fila_processamento_status` são semeados fixos pela migration da Fase
0 (1=pendente ... 9=dlq) — é o que permite o índice parcial de claim
(`WHERE status_id = 1`). Ainda assim nenhum número aparece no SQL do motor: o
`codigo -> id` é resolvido aqui, uma vez, e o resto do código usa só o código.

Os tipos também são carregados uma vez. `ativo` é a única coluna cuja leitura
não pode ficar velha (é o interruptor que drena um tipo antes de um deploy),
então ela NÃO vem do cache: o claim filtra por `t.ativo` direto no SQL.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

# Códigos de status, na ordem em que a migration da Fase 0 os semeou.
PENDENTE = "pendente"
RESERVADO = "reservado"
EXECUTANDO = "executando"
AGUARDANDO_CALLBACK = "aguardando_callback"
CONCLUIDO = "concluido"
FALHOU = "falhou"
INCERTO = "incerto"
CANCELADO = "cancelado"
DLQ = "dlq"

CODIGOS_OBRIGATORIOS = (
    PENDENTE,
    RESERVADO,
    EXECUTANDO,
    AGUARDANDO_CALLBACK,
    CONCLUIDO,
    FALHOU,
    INCERTO,
    CANCELADO,
    DLQ,
)

CLASSE_SINCRONO = "sincrono"
CLASSE_ASSINCRONO = "assincrono"


@dataclass(frozen=True)
class Tipo:
    """Uma linha de `fila_processamento_tipos`, sem `ativo` (ver módulo)."""

    codigo: str
    nome: str
    destino: str
    classe_padrao: str
    prioridade_padrao: int
    max_tentativas_padrao: int
    timeout_segundos: int
    concorrencia_maxima: Optional[int]
    idempotente: bool
    tipo_verificacao_codigo: Optional[str]


@dataclass(frozen=True)
class Catalogo:
    status: dict            # codigo -> id
    status_por_id: dict     # id -> codigo
    tipos: dict             # codigo -> Tipo

    def id_status(self, codigo: str) -> int:
        try:
            return self.status[codigo]
        except KeyError as exc:
            raise RuntimeError(f"Status '{codigo}' não existe em fila_processamento_status") from exc

    def codigo_status(self, id_status: Optional[int]) -> Optional[str]:
        if id_status is None:
            return None
        return self.status_por_id.get(id_status)

    def tipo(self, codigo: str) -> Optional[Tipo]:
        return self.tipos.get(codigo)


def carregar_catalogo(conn) -> Catalogo:
    """Lê os dois catálogos. Chamado no `montar_aplicacao()`; um tipo novo no
    banco só passa a valer no próximo start do worker — que é o mesmo deploy
    que traz o handler dele."""
    status = dict(conn.execute(text("SELECT codigo, id FROM fila_processamento_status")).all())
    faltando = [codigo for codigo in CODIGOS_OBRIGATORIOS if codigo not in status]
    if faltando:
        raise RuntimeError(f"Catálogo de status da fila incompleto no banco: faltam {faltando}")

    tipos = {}
    for linha in conn.execute(text("""
        SELECT codigo, nome, destino, classe_padrao, prioridade_padrao, max_tentativas_padrao,
               timeout_segundos, concorrencia_maxima, idempotente, tipo_verificacao_codigo
          FROM fila_processamento_tipos
         ORDER BY codigo
    """)).mappings():
        tipos[linha["codigo"]] = Tipo(
            codigo=linha["codigo"],
            nome=linha["nome"],
            destino=linha["destino"],
            classe_padrao=linha["classe_padrao"],
            prioridade_padrao=int(linha["prioridade_padrao"]),
            max_tentativas_padrao=int(linha["max_tentativas_padrao"]),
            timeout_segundos=int(linha["timeout_segundos"]),
            concorrencia_maxima=linha["concorrencia_maxima"],
            idempotente=bool(linha["idempotente"]),
            tipo_verificacao_codigo=linha["tipo_verificacao_codigo"],
        )

    return Catalogo(
        status=status,
        status_por_id={id_: codigo for codigo, id_ in status.items()},
        tipos=tipos,
    )
