"""Peças comuns aos dois handlers de ESCRITA do PCP (orçamento e aprovação).

Nasceram nos despachos antigos e foram deliberadamente mantidas quando os
handlers da fila os substituíram: uma decide o que entra na CHAMADA ao ERP
(o par `assincrono`/`url_webhook`) e a outra o que entra na AUDITORIA
(`payload_enviado`, sem o token do webhook). Duplicá-las por handler seria
abrir a porta para os dois divergirem.
"""

from typing import Optional


def corpo_para_auditoria(corpo: dict) -> dict:
    """O que vai para `payload_enviado`: o corpo real, sem a url_webhook — ela
    carrega o token que autentica o webhook e aparece na tela do PageFlow."""
    copia = dict(corpo)
    if copia.get("url_webhook"):
        copia["url_webhook"] = "[omitida]"
    return copia


def com_modo_assincrono(corpo: dict, modo_envio: Optional[str], url_webhook: Optional[str]) -> dict:
    if modo_envio == "assincrono" and url_webhook:
        return {**corpo, "assincrono": True, "url_webhook": url_webhook}
    return corpo
