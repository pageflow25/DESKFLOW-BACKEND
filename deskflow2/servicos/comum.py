"""Peças comuns aos despachos de orçamento e de aprovação."""

import logging
from typing import Optional

from ..clientes.erp import ErpClient, ErpIndisponivel, ResultadoIncerto, ler_json, mensagem_de_erro, sucesso_erp
from .repasse import extrair_id_requisicao

logger = logging.getLogger(__name__)

MENSAGEM_INCERTO = (
    "Sem confirmação do ERP ({motivo}). Confira no ERP antes de reenviar: "
    "a chamada pode ter sido processada."
)


def corpo_erro(mensagem: str) -> dict:
    """Formato de erro que o PageFlow entende (contrato da fila, seção 12)."""
    return {"success": False, "message": mensagem}


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


class ResultadoChamada:
    """O que fazer com a resposta de um POST ao ERP.

    - `id_requisicao`: ack assíncrono — gravar e parar, o ERP entrega no PageFlow;
    - `corpo_retorno`: repassar ao PageFlow como veio (sucesso ou erro).
    """

    def __init__(self, id_requisicao: Optional[int] = None, corpo_retorno: Optional[dict] = None):
        self.id_requisicao = id_requisicao
        self.corpo_retorno = corpo_retorno


def chamar_erp(erp: ErpClient, caminho: str, corpo: dict, modo_envio: Optional[str], chave_resultado: str) -> ResultadoChamada:
    """POST ao ERP e classificação da resposta. `chave_resultado` é o campo que
    só existe no resultado final (`id_orcamento` no orçamento, a lista `data`
    na aprovação) — sem ele, uma resposta de sucesso com `id_requisicao` é o
    ack da chamada assíncrona."""
    try:
        resposta = erp.post(caminho, corpo)
    except ResultadoIncerto as exc:
        logger.error("ERP: %s sem confirmação: %s", caminho, exc)
        return ResultadoChamada(corpo_retorno=corpo_erro(MENSAGEM_INCERTO.format(motivo=exc)))
    except ErpIndisponivel as exc:
        logger.error("ERP indisponível em %s: %s", caminho, exc)
        return ResultadoChamada(corpo_retorno=corpo_erro(f"ERP indisponível: {exc}"))

    dados = ler_json(resposta)
    if not isinstance(dados, dict):
        return ResultadoChamada(corpo_retorno=corpo_erro(
            f"Resposta inesperada do ERP (HTTP {resposta.status_code}): {mensagem_de_erro(dados, resposta)}"
        ))

    if modo_envio == "assincrono" and sucesso_erp(dados):
        interno = dados.get("data")
        tem_resultado = isinstance(interno, list) if chave_resultado == "data" else (
            isinstance(interno, dict) and interno.get(chave_resultado) is not None
        )
        id_requisicao = extrair_id_requisicao(dados)
        if id_requisicao and not tem_resultado:
            return ResultadoChamada(id_requisicao=id_requisicao)

    return ResultadoChamada(corpo_retorno=dados)
