"""Monta o `data` do POST /api/v1/proposta/aprovar a partir de `sql/aprovacao.sql`.

Antes o JSON vinha pronto no payload do item (`itens_aprovados`, montado pelo
PageFlow). Agora sai do SQL, ao lado dos SQLs de orçamento, para poder ser
ajustado sem mexer no PageFlow. O SQL lê tudo pela aprovação: o orçamento, o
`id_orcamento` do ERP, o `gerar_op`, os itens devolvidos pelo ERP com as datas
e, em lote com entregas por distribuição, as `entregas[]`.
"""

from functools import lru_cache

from sqlalchemy import text

from .payload import PASTA_SQL, remover_nulos

ARQUIVO_APROVACAO = "aprovacao.sql"


@lru_cache
def _consulta():
    return text((PASTA_SQL / ARQUIVO_APROVACAO).read_text(encoding="utf-8-sig"))


def montar_dados_aprovacao(conn, aprovacao_id: int):
    """O `data` da aprovação (`{id_orcamento, gerar_op, itens}`), ou `None`
    quando a aprovação não existe no banco."""
    linhas = conn.execute(_consulta(), {"aprovacao_id": aprovacao_id}).scalars().all()
    if not linhas:
        return None
    return remover_nulos(linhas[0])
