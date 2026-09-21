"""Monta o corpo do POST /api/v1/orcamento de um orçamento do PCP.

Roda o SQL do modo do lote (`sql/orcamento_unidade.sql` ou
`sql/orcamento_escola.sql`) só com os pedidos daquele orçamento e confere o
resultado antes de mandar ao ERP: um pedido que o SQL descarta (sem arquivo,
sem especificação, produto fora do catálogo, quantidade zero) ficaria fora do
orçamento sem ninguém perceber.
"""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Integer, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from ..repositorios.fila import OrcamentoReivindicado

PASTA_SQL = Path(__file__).resolve().parent.parent / "sql"
ARQUIVO_POR_MODO = {
    "unidade": "orcamento_unidade.sql",
    "escola": "orcamento_escola.sql",
}


class PayloadIncompleto(Exception):
    """O orçamento não pode ser montado com os dados do banco."""


@lru_cache
def _consulta(modo_agrupamento: str):
    arquivo = ARQUIVO_POR_MODO.get(modo_agrupamento)
    if not arquivo:
        raise PayloadIncompleto(f"Modo de agrupamento desconhecido: {modo_agrupamento!r}")
    sql = (PASTA_SQL / arquivo).read_text(encoding="utf-8-sig")
    return text(sql).bindparams(bindparam("pedido_distribuicao_ids", type_=ARRAY(Integer)))


def ids_do_codigo_externo(codigo_externo) -> set:
    if codigo_externo is None:
        return set()
    return {int(parte) for parte in str(codigo_externo).split(",") if parte.strip().isdigit()}


def remover_nulos(valor):
    """O ERP converte `null` em 0 nos campos inteiros: chave sem valor não vai."""
    if isinstance(valor, dict):
        return {chave: remover_nulos(item) for chave, item in valor.items() if item is not None}
    if isinstance(valor, list):
        return [remover_nulos(item) for item in valor]
    return valor


def validar_cabecalho(orcamento: OrcamentoReivindicado) -> None:
    faltando_cabecalho = [
        campo for campo, valor in (
            ("cliente", orcamento.cliente_id),
            ("vendedor", orcamento.vendedor_id),
            ("forma de pagamento", orcamento.forma_pagamento),
        ) if valor is None
    ]
    if faltando_cabecalho:
        raise PayloadIncompleto(
            f"Unidade sem {', '.join(faltando_cabecalho)} cadastrado(s) para o orçamento — "
            "corrija o cadastro da unidade e reenvie"
        )


def validar_itens(payload: dict, orcamento: OrcamentoReivindicado) -> None:
    itens = (payload.get("data") or {}).get("itens") or []
    if not itens:
        raise PayloadIncompleto("Nenhum item do orçamento pôde ser montado")

    esperados = set(orcamento.pedido_distribuicao_ids)
    montados = set()
    for item in itens:
        montados |= ids_do_codigo_externo(item.get("codigo_externo"))

    faltando = sorted(esperados - montados)
    sobrando = sorted(montados - esperados)
    if faltando or sobrando:
        partes = []
        if faltando:
            partes.append(
                f"pedidos sem item no orçamento: {faltando} "
                "(sem arquivo/especificação, produto fora do catálogo Bremen ou quantidade zero)"
            )
        if sobrando:
            partes.append(f"pedidos que não são deste orçamento: {sobrando}")
        raise PayloadIncompleto("Orçamento incompleto — " + "; ".join(partes))


def montar_payload_orcamento(conn, orcamento: OrcamentoReivindicado, identifier: str) -> dict:
    if not orcamento.pedido_distribuicao_ids:
        raise PayloadIncompleto("Orçamento sem pedidos vinculados")
    validar_cabecalho(orcamento)

    linhas = conn.execute(_consulta(orcamento.modo_agrupamento), {
        "pedido_distribuicao_ids": list(orcamento.pedido_distribuicao_ids),
        "id_cliente": orcamento.cliente_id,
        "id_vendedor": orcamento.vendedor_id,
        "id_forma_pagamento": None if orcamento.forma_pagamento is None else str(orcamento.forma_pagamento),
    }).scalars().all()

    if not linhas:
        raise PayloadIncompleto(
            f"Nenhum item montado para os pedidos {sorted(orcamento.pedido_distribuicao_ids)} "
            "(sem arquivo/especificação ou produto fora do catálogo Bremen)"
        )
    if len(linhas) > 1:
        raise PayloadIncompleto(
            f"Os pedidos do orçamento geraram {len(linhas)} orçamentos no modo {orcamento.modo_agrupamento} "
            "— a divisão do PageFlow e a do SQL não bateram"
        )

    payload = remover_nulos(linhas[0])
    validar_itens(payload, orcamento)
    return {"identifier": identifier, **payload}
