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

from ..clientes.erp import sucesso_erp
from ..fila.modelos import Desfecho, ItemReivindicado, Preparo
from .comum import (
    PayloadInvalido,
    classificar_falha,
    clientes_do_envelope,
    documento_do_payload,
    envelope_json,
)

TIPO = "cliente.consultar"


def montar_consulta(payload: dict) -> dict:
    """Traduz o payload do item nos parâmetros do GET.

    Os quatro do contrato do ERP: `id`, `cpfcnpj`, `email` e `page`. O
    documento vem normalizado (só dígitos), que é a forma com que o PageFlow o
    grava na chave de bloqueio.
    """
    payload = payload or {}
    consulta: dict = {}

    identificador = payload.get("id_cliente", payload.get("id"))
    if identificador not in (None, ""):
        try:
            consulta["id_cliente"] = int(identificador)
        except (TypeError, ValueError):
            pass

    documento = documento_do_payload(payload)
    if documento:
        consulta["cpfcnpj"] = documento

    email = (payload.get("email") or "").strip() if isinstance(payload.get("email"), str) else None
    if email:
        consulta["email"] = email

    pagina = payload.get("page", payload.get("pagina"))
    if pagina not in (None, ""):
        try:
            consulta["page"] = int(pagina)
        except (TypeError, ValueError):
            pass

    return consulta


class HandlerClienteConsultar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        consulta = montar_consulta(item.payload)
        if not consulta:
            invalido = PayloadInvalido(
                "cliente.consultar exige ao menos um critério (id_cliente, documento, email ou page).")
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)

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
            "consulta": montar_consulta(item.payload),
            "metadata": metadata,
        })

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Leitura idempotente nunca chega a `incerto`; se chegar, a retentativa
        já é segura e quem decide é o motor."""
        return None
