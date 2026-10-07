"""`cliente.atualizar` — PATCH /api/v1/cliente. NÃO idempotente.

Mesmo regime de `cliente.criar`: resultado incerto nunca é retentado
automaticamente pelo motor (`tipos.idempotente = false`).

O `verificar()` aqui NÃO pode ser "o cliente existe?" — numa atualização ele
existe sempre, e responder `concluido` por isso fecharia como aplicada uma
edição que talvez nem tenha saído. A pergunta certa é **o ERP já está com o que
mandamos?**:

- o registro do ERP bate com os campos enviados: a chamada foi aplicada, o item
  fecha como `concluido`;
- diverge: o PATCH não chegou a valer. Volta a `pendente` — e repetir é seguro
  porque o corpo leva os ids de contato e endereço (é a omissão desses ids que
  duplicaria os registros no ERP, e o PageFlow os preserva em `mesclarComAtual`);
- o cliente não existe: não há o que atualizar. Também volta a `pendente`, onde
  a próxima tentativa recebe a recusa explícita do ERP e vira falha definitiva
  com mensagem de verdade, em vez de fechar como sucesso silencioso;
- a consulta falhou: `None`, o item segue `incerto` para decisão humana.
"""

from typing import Any, Optional

from ...fila.modelos import Desfecho, ItemReivindicado, Preparo
from ...integracoes.erp import ErroErp, envelope_json, sucesso_erp
from ...servicos.clientes.verificacao import consultar_por_documento, divergencias, id_cliente_de
from ..payload_invalido import PayloadInvalido
from ..resposta_erp import classificar_falha
from .validators import cliente_validator

TIPO = "cliente.atualizar"

class HandlerClienteAtualizar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        payload = item.payload or {}

        corpo, invalido = cliente_validator.validar_atualizar(payload)
        if invalido is not None:
            return invalido.preparo()
        return Preparo(
            payload_enviado={"metodo": "PATCH", "caminho": "/api/v1/cliente", "data": corpo},
            chamar=lambda: self._erp.atualizar_cliente(corpo),
        )

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        dados = envelope_json(bruto)
        if bruto.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(bruto, dados, mutacao=True)

        payload = item.payload or {}
        cliente = payload.get("cliente") or {}
        id_cliente = id_cliente_de({"id_cliente": payload.get("id_cliente", cliente.get("id_cliente"))})
        return Desfecho.concluido({"id_cliente": id_cliente, "resposta": dados})

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        payload = item.payload or {}
        cliente = payload.get("cliente") or {}
        documento = cliente_validator.documento_do_payload(payload)
        if not documento or not isinstance(cliente, dict):
            return None

        try:
            registro = consultar_por_documento(self._erp, documento)
        except ErroErp:
            return None

        if registro is None:
            return Desfecho.retentar(
                "Verificação no ERP: cliente não encontrado pelo documento, a alteração não foi aplicada.",
                "VERIFICACAO_NAO_ENCONTRADO",
            )

        fora = divergencias(cliente, registro)
        if fora:
            return Desfecho.retentar(
                "Verificação no ERP: o cadastro ainda não reflete a alteração "
                f"({', '.join(sorted(fora))}).",
                "VERIFICACAO_NAO_APLICADA",
            )

        id_cliente = id_cliente_de(registro) or id_cliente_de(
            {"id_cliente": payload.get("id_cliente", cliente.get("id_cliente"))})
        return Desfecho.concluido({
            "id_cliente": id_cliente,
            "resposta": {"success": True, "data": registro},
            "verificado_por": "cliente.consultar",
        })
