"""Ids dos catálogos de status do PCP, sempre resolvidos pelo `codigo`.

O id de `pendente_envio` muda de ambiente para ambiente (foi inserido por
migration depois dos outros), então nenhum id é fixo no código.
"""

from dataclasses import dataclass

from sqlalchemy import text

PENDENTE_ENVIO = "pendente_envio"
AGUARDANDO_RETORNO = "aguardando_retorno"
SUCESSO = "sucesso"
ERRO = "erro"

EM_FILA = "em_fila"
EM_PROCESSAMENTO = "em_processamento"


@dataclass(frozen=True)
class CatalogoStatus:
    # orcamento_envio_requisicao_status (orçamentos e aprovações)
    chamada: dict
    # orcamento_envio_parametro_status (lote)
    lote: dict

    def id_chamada(self, codigo: str) -> int:
        return self.chamada[codigo]

    def id_lote(self, codigo: str) -> int:
        return self.lote[codigo]


_cache: CatalogoStatus | None = None


def carregar_catalogo(conn) -> CatalogoStatus:
    global _cache
    if _cache is None:
        chamada = dict(conn.execute(text("SELECT codigo, id FROM orcamento_envio_requisicao_status")).all())
        lote = dict(conn.execute(text("SELECT codigo, id FROM orcamento_envio_parametro_status")).all())
        faltando = [c for c in (PENDENTE_ENVIO, AGUARDANDO_RETORNO, SUCESSO, ERRO) if c not in chamada]
        faltando += [c for c in (EM_FILA, EM_PROCESSAMENTO) if c not in lote]
        if faltando:
            raise RuntimeError(f"Catálogo de status do PCP incompleto no banco: faltam {faltando}")
        _cache = CatalogoStatus(chamada=chamada, lote=lote)
    return _cache


def limpar_cache() -> None:
    global _cache
    _cache = None
