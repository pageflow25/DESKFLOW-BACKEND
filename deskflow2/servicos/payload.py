"""Monta o corpo do POST /api/v1/orcamento de um orçamento do PCP.

O SQL é escolhido pela ORIGEM do lote e, na origem escola, pelo modo de
agrupamento:
  - escola + modo 'unidade'  -> `sql/orcamento_unidade.sql`;
  - escola + modo 'escola'   -> `sql/orcamento_escola.sql`;
  - integração               -> `sql/orcamento_integracao.sql`.

Ele roda só com os ids daquele orçamento e o resultado é conferido antes de ir
ao ERP: um item que o SQL descarta (sem arquivo, sem especificação, produto
fora do catálogo, quantidade zero) ficaria fora do orçamento sem ninguém
perceber. A conferência é pelo `codigo_externo`, que carrega os ids de ORIGEM
— pedido de escola ou produto de pedido de integração, conforme o lote.
"""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Integer, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from ..repositorios.fila import ORIGEM_INTEGRACAO, OrcamentoReivindicado

PASTA_SQL = Path(__file__).resolve().parent.parent / "sql"
ARQUIVO_POR_MODO = {
    "unidade": "orcamento_unidade.sql",
    "escola": "orcamento_escola.sql",
}
ARQUIVO_INTEGRACAO = "orcamento_integracao.sql"

# Nome do parâmetro de lista em cada SQL (array de verdade no bind).
PARAMETRO_IDS_ESCOLA = "pedido_distribuicao_ids"
PARAMETRO_IDS_INTEGRACAO = "integra_pedido_produto_ids"


class PayloadIncompleto(Exception):
    """O orçamento não pode ser montado com os dados do banco."""


def _arquivo_e_parametro(orcamento: OrcamentoReivindicado):
    """(arquivo SQL, nome do parâmetro de ids) para a origem do orçamento."""
    if orcamento.origem == ORIGEM_INTEGRACAO:
        return ARQUIVO_INTEGRACAO, PARAMETRO_IDS_INTEGRACAO
    arquivo = ARQUIVO_POR_MODO.get(orcamento.modo_agrupamento)
    if not arquivo:
        raise PayloadIncompleto(f"Modo de agrupamento desconhecido: {orcamento.modo_agrupamento!r}")
    return arquivo, PARAMETRO_IDS_ESCOLA


@lru_cache
def _consulta(arquivo: str, parametro_ids: str):
    sql = (PASTA_SQL / arquivo).read_text(encoding="utf-8-sig")
    return text(sql).bindparams(bindparam(parametro_ids, type_=ARRAY(Integer)))


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
    """Cliente, vendedor e forma de pagamento são obrigatórios nas duas origens.

    Muda só de ONDE o PageFlow os copiou: do cadastro da unidade escolar
    (origem escola) ou do cadastro da integração (origem integração). A
    mensagem aponta para o cadastro certo.
    """
    faltando_cabecalho = [
        campo for campo, valor in (
            ("cliente", orcamento.cliente_id),
            ("vendedor", orcamento.vendedor_id),
            ("forma de pagamento", orcamento.forma_pagamento),
        ) if valor is None
    ]
    if faltando_cabecalho:
        cadastro = "da integração" if orcamento.origem == ORIGEM_INTEGRACAO else "da unidade"
        origem = "Integração" if orcamento.origem == ORIGEM_INTEGRACAO else "Unidade"
        raise PayloadIncompleto(
            f"{origem} sem {', '.join(faltando_cabecalho)} cadastrado(s) para o orçamento — "
            f"corrija o cadastro {cadastro} e reenvie"
        )


def validar_itens(payload: dict, orcamento: OrcamentoReivindicado) -> None:
    """Todo id de origem do orçamento tem de aparecer em algum `codigo_externo`,
    e nenhum id de fora pode aparecer. Vale igual nas duas origens: só muda o
    que o id significa (pedido de escola ou produto de pedido de integração).
    """
    itens = (payload.get("data") or {}).get("itens") or []
    if not itens:
        raise PayloadIncompleto("Nenhum item do orçamento pôde ser montado")

    rotulo = "produtos" if orcamento.origem == ORIGEM_INTEGRACAO else "pedidos"
    motivo = (
        "(sem modelo de catálogo Bremen ou pedido removido)"
        if orcamento.origem == ORIGEM_INTEGRACAO
        else "(sem arquivo/especificação, produto fora do catálogo Bremen ou quantidade zero)"
    )

    esperados = set(orcamento.ids_origem)
    montados = set()
    for item in itens:
        montados |= ids_do_codigo_externo(item.get("codigo_externo"))

    faltando = sorted(esperados - montados)
    sobrando = sorted(montados - esperados)
    if faltando or sobrando:
        partes = []
        if faltando:
            partes.append(f"{rotulo} sem item no orçamento: {faltando} {motivo}")
        if sobrando:
            partes.append(f"{rotulo} que não são deste orçamento: {sobrando}")
        raise PayloadIncompleto("Orçamento incompleto — " + "; ".join(partes))


def montar_payload_orcamento(conn, orcamento: OrcamentoReivindicado, identifier: str) -> dict:
    ids = list(orcamento.ids_origem)
    if not ids:
        raise PayloadIncompleto("Orçamento sem pedidos vinculados")
    validar_cabecalho(orcamento)

    arquivo, parametro_ids = _arquivo_e_parametro(orcamento)
    parametros = {
        parametro_ids: ids,
        "id_cliente": orcamento.cliente_id,
        "id_vendedor": orcamento.vendedor_id,
        "id_forma_pagamento": None if orcamento.forma_pagamento is None else str(orcamento.forma_pagamento),
    }
    if orcamento.origem == ORIGEM_INTEGRACAO:
        # Só o SQL de integração declara :data_entrega — passar o parâmetro
        # para um SQL que não o usa faria o SQLAlchemy reclamar.
        parametros["data_entrega"] = orcamento.data_entrega
    linhas = conn.execute(_consulta(arquivo, parametro_ids), parametros).scalars().all()

    if not linhas:
        raise PayloadIncompleto(
            f"Nenhum item montado para {sorted(ids)} "
            "(sem arquivo/especificação ou produto fora do catálogo Bremen)"
        )
    if len(linhas) > 1:
        divisao = "integração" if orcamento.origem == ORIGEM_INTEGRACAO else f"modo {orcamento.modo_agrupamento}"
        raise PayloadIncompleto(
            f"Os itens do orçamento geraram {len(linhas)} orçamentos na divisão de {divisao} "
            "— a divisão do PageFlow e a do SQL não bateram"
        )

    payload = remover_nulos(linhas[0])
    validar_itens(payload, orcamento)
    return {"identifier": identifier, **payload}
