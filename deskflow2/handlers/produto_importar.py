"""`produto.importar` — GET /api/v1/caracteristicasproduto. Idempotente.

Este é o ponto de corte que mais ganha com a fila, e não por desempenho: era o
único dos cinco que chamava o ERP sem auditoria nenhuma. Passando por aqui ele
ganha timeline, solicitante, tentativas e reprocesso.

O handler faz SÓ a busca. A persistência em cascata (item, componentes,
perguntas, respostas) fica no projetor do PageFlow, com a mesma
`persistirProdutoDoErp` do caminho direto — a separação existe justamente para
não haver duas implementações da gravação.

Formato do payload: `{id_produto: <int>, id_categoria: <int>, origem_erp: 2}`.
Formato do resultado, lido por `services/fila/projetores/produto.importar.js`:
`{"produto": <o objeto data[0] da resposta>}`.

`id_categoria` não vai ao ERP, mas é validado aqui de propósito: sem ele o
projetor lança na hora de aplicar, com o item já `concluido` e a chamada já
gasta. Validando no preparo, o erro aparece antes de qualquer I/O, como falha
definitiva e sem consumir tentativa.
"""

from typing import Any, Optional

from ..clientes.erp import ORIGEM_MODELO_DE_PRODUTO, sucesso_erp
from ..fila.modelos import Desfecho, ItemReivindicado, Preparo
from .comum import (
    PayloadInvalido,
    classificar_falha,
    envelope_json,
    inteiro_positivo,
    registros_do_envelope,
)

TIPO = "produto.importar"


def origem_do_payload(payload: Optional[dict]) -> int:
    """`origem_erp` do payload, com 2 (modelo de produto) como padrão — o mesmo
    default de `buscarCaracteristicasProduto` no PageFlow."""
    return inteiro_positivo((payload or {}).get("origem_erp")) or ORIGEM_MODELO_DE_PRODUTO


class HandlerProdutoImportar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        payload = item.payload or {}
        id_produto = inteiro_positivo(payload.get("id_produto"))
        if id_produto is None:
            invalido = PayloadInvalido("produto.importar exige `id_produto` inteiro e maior que zero.")
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)
        if inteiro_positivo(payload.get("id_categoria")) is None:
            invalido = PayloadInvalido(
                "produto.importar exige `id_categoria` inteiro e maior que zero: sem ela o "
                "projetor do PageFlow não tem onde gravar o produto importado.")
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)

        origem = origem_do_payload(payload)
        return Preparo(
            payload_enviado={
                "metodo": "GET",
                "caminho": "/api/v1/caracteristicasproduto",
                "params": {"id": id_produto, "origem": origem},
            },
            chamar=lambda: self._erp.buscar_caracteristicas_produto(id_produto, origem),
        )

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        dados = envelope_json(bruto)
        if bruto.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(bruto, dados, mutacao=False)

        payload = item.payload or {}
        id_produto = inteiro_positivo(payload.get("id_produto"))
        registros = registros_do_envelope(dados)
        if not registros:
            # O ERP respondeu a busca e não conhece o produto. Repetir não muda
            # o resultado: falha definitiva, com o motivo na tela.
            return Desfecho.falhou(
                f"ERP respondeu sucesso sem características para o produto {id_produto}.",
                "ERP_PRODUTO_SEM_CARACTERISTICAS",
            )

        return Desfecho.concluido({
            "produto": registros[0],
            "id_produto": id_produto,
            "id_categoria": inteiro_positivo(payload.get("id_categoria")),
            "origem_erp": origem_do_payload(payload),
        })

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Leitura idempotente: o motor retenta sozinho, não há o que verificar."""
        return None
