"""Regras de payload de `produto.importar`."""

from typing import Optional, Tuple

from .comum import PayloadInvalido, inteiro_positivo


def validar_importar(payload: Optional[dict]) -> Tuple[Optional[dict], Optional[PayloadInvalido]]:
    """`id_produto` e `id_categoria`, os dois obrigatórios.

    A categoria é exigida por um motivo que não é do ERP e sim do outro lado:
    sem ela o projetor do PageFlow não tem onde gravar o produto importado. Vale
    recusar aqui, antes da chamada, em vez de importar e descobrir depois que o
    resultado não tem destino.
    """
    payload = payload or {}

    id_produto = inteiro_positivo(payload.get("id_produto"))
    if id_produto is None:
        return None, PayloadInvalido("produto.importar exige `id_produto` inteiro e maior que zero.")

    id_categoria = inteiro_positivo(payload.get("id_categoria"))
    if id_categoria is None:
        return None, PayloadInvalido(
            "produto.importar exige `id_categoria` inteiro e maior que zero: sem ela o "
            "projetor do PageFlow não tem onde gravar o produto importado.")

    return {"id_produto": id_produto, "id_categoria": id_categoria}, None
