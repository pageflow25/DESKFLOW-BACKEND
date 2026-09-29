"""`cliente.criar` — POST /api/v1/cliente. NÃO idempotente.

`id_cliente` vira FK de `escola_unidades` no PageFlow, então um cliente
duplicado é caro de desfazer: um resultado incerto desta chamada NUNCA é
retentado automaticamente (o motor já garante isso pelo `tipos.idempotente`).

O que fecha o requisito da Fase 2a é o `verificar()`: diante de um resultado
incerto, ele consulta o documento no ERP e resolve sozinho —

- **encontrou** o cliente: a chamada TINHA acontecido. O item fecha como
  `concluido` com o `id_cliente` verdadeiro, e o projetor espelha como se a
  resposta tivesse chegado;
- **não encontrou**: o ERP respondeu a busca e o cliente não existe, então o
  POST não foi processado. Volta a `pendente` com backoff — repetir é seguro
  porque a ausência foi confirmada, não presumida;
- **a consulta falhou**: `None`. O item fica `incerto` e vai para a decisão
  humana. Consulta que não pôde ser concluída não é prova de ausência — é
  exatamente o falso negativo silencioso do cliente legado do PageFlow.

Contrato do resultado, lido por `services/fila/projetores/cliente.criar.js`:
`{"id_cliente": <int>, "resposta": <envelope do ERP>}`.
"""

from typing import Any, Optional

from ..clientes.erp import ErroErp, sucesso_erp
from ..fila.modelos import Desfecho, ItemReivindicado, Preparo
from .comum import (
    PayloadInvalido,
    classificar_falha,
    clientes_do_envelope,
    consultar_por_documento,
    documento_do_payload,
    envelope_json,
    id_cliente_de,
)

TIPO = "cliente.criar"


class HandlerClienteCriar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        payload = item.payload or {}
        cliente = payload.get("cliente")
        if not isinstance(cliente, dict) or not cliente:
            invalido = PayloadInvalido("cliente.criar exige `payload.cliente` com o cadastro a enviar.")
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)
        if not documento_do_payload(payload):
            invalido = PayloadInvalido(
                "cliente.criar exige CNPJ ou CPF: sem documento não há como verificar um resultado incerto.")
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)

        return Preparo(
            payload_enviado={"metodo": "POST", "caminho": "/api/v1/cliente", "data": cliente},
            chamar=lambda: self._erp.criar_cliente(cliente),
        )

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        dados = envelope_json(bruto)
        if bruto.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(bruto, dados, mutacao=True)

        interno = dados.get("data") if isinstance(dados, dict) else None
        id_cliente = id_cliente_de(interno if isinstance(interno, dict) else None)
        if id_cliente is None:
            # Sucesso sem id: o cadastro provavelmente existe e o identificador
            # se perdeu. É incerto, e o `verificar` recupera o id pela consulta.
            return Desfecho.incerto(
                "ERP respondeu sucesso sem id_cliente na criação.", "ERP_SUCESSO_SEM_ID")

        return Desfecho.concluido({"id_cliente": id_cliente, "resposta": dados})

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        documento = documento_do_payload(item.payload)
        if not documento:
            return None

        try:
            registro = consultar_por_documento(self._erp, documento)
        except ErroErp:
            return None

        if registro is None:
            return Desfecho.retentar(
                "Verificação no ERP: o cliente não existe, o envio não foi processado.",
                "VERIFICACAO_NAO_CRIADO",
            )

        id_cliente = id_cliente_de(registro)
        if id_cliente is None:
            # Existe mas sem id utilizável: o projetor não teria o que espelhar.
            return None

        return Desfecho.concluido({
            "id_cliente": id_cliente,
            "resposta": {"success": True, "data": registro},
            "verificado_por": "cliente.consultar",
            "clientes": clientes_do_envelope({"data": registro}),
        })
