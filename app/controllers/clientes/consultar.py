"""`cliente.consultar` — GET /api/v1/cliente.

Leitura, e idempotente: repetir é grátis, então nenhuma resposta dela vira
`incerto` e ela não precisa de verificação — por isso `verificar()` devolve
sempre `None`.

O resultado não é descartado: o projetor `services/fila/projetores/cliente.consultar.js`
espelha cada registro em `bremen_clientes`, que é o que sustenta a decisão de
que nenhuma tela do PageFlow chame o ERP direto. O contrato do resultado é o que
aquele projetor lê: `{"clientes": [...]}`.
"""

from typing import Any, Optional

from ...integracoes.erp import sucesso_erp
from ...fila.modelos import Desfecho, ItemReivindicado, Preparo
from ..comum import (
    classificar_falha,
    clientes_do_envelope,
    envelope_json,
)
from ...validadores import clientes as validadores
from ...validadores.comum import PayloadInvalido

TIPO = "cliente.consultar"


class HandlerClienteConsultar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        consulta, invalido = validadores.validar_consultar(item.payload)
        if invalido is not None:
            return invalido.preparo()

        return Preparo(
            payload_enviado={"metodo": "GET", "caminho": "/api/v1/cliente", "params": consulta},
            chamar=lambda: self._erp.listar_clientes(**consulta),
        )

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        dados = envelope_json(bruto)
        if bruto.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(bruto, dados, mutacao=False)

        clientes = clientes_do_envelope(dados)
        metadata = dados.get("metadata") if isinstance(dados.get("metadata"), dict) else None
        return Desfecho.concluido({
            "clientes": clientes,
            "total": len(clientes),
            "consulta": validadores.montar_consulta(item.payload),
            "metadata": metadata,
        })

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Leitura idempotente nunca chega a `incerto`; se chegar, a retentativa
        já é segura e quem decide é o motor."""
        return None
