"""Repasse de resultados ao PageFlow (POST /api/pcp/retorno/...).

O PageFlow é o único que grava o resultado do ERP. O retorno é idempotente
(repetir o mesmo repasse devolve `ja_processado`), então 5xx e falha de rede
são repetidos. 404/409/400 não adiantam repetir: a linha não existe, foi
substituída por um reenvio ou o corpo foi recusado — loga e descarta.
"""

import enum
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class Desfecho(enum.Enum):
    ENTREGUE = "entregue"
    DESCARTADO = "descartado"
    NAO_ENTREGUE = "nao_entregue"


@dataclass
class ResultadoRepasse:
    desfecho: Desfecho
    status_http: Optional[int] = None
    dados: Any = None


class PageflowClient:
    def __init__(self, settings, http: Optional[httpx.Client] = None, dormir: Callable[[float], None] = time.sleep):
        self._base = settings.PAGEFLOW_API_URL
        self._tentativas = max(1, settings.PAGEFLOW_TENTATIVAS)
        self._http = http or httpx.Client(timeout=settings.PAGEFLOW_TIMEOUT)
        self._headers = {"x-api-key": settings.PAGEFLOW_API_KEY, "Content-Type": "application/json"}
        self._dormir = dormir

    def retorno_orcamento(self, orcamento_id: int, corpo: dict) -> ResultadoRepasse:
        return self._post(f"/api/pcp/retorno/orcamentos/{orcamento_id}", corpo)

    def retorno_aprovacao(self, aprovacao_id: int, corpo: dict) -> ResultadoRepasse:
        return self._post(f"/api/pcp/retorno/aprovacoes/{aprovacao_id}", corpo)

    def consulta_previa(self, aprovacao_id: int, corpo: dict) -> ResultadoRepasse:
        return self._post(f"/api/pcp/retorno/aprovacoes/{aprovacao_id}/consulta", corpo)

    def downloads(self, aprovacao_id: int, corpo: dict) -> ResultadoRepasse:
        return self._post(f"/api/pcp/retorno/aprovacoes/{aprovacao_id}/downloads", corpo)

    def _post(self, caminho: str, corpo: dict) -> ResultadoRepasse:
        ultimo_status = None
        for tentativa in range(1, self._tentativas + 1):
            try:
                resposta = self._http.post(f"{self._base}{caminho}", json=corpo, headers=self._headers)
            except httpx.HTTPError as exc:
                logger.warning("PageFlow: %s falhou (%s), tentativa %s/%s", caminho, exc, tentativa, self._tentativas)
            else:
                ultimo_status = resposta.status_code
                if resposta.status_code < 300:
                    dados = _json_ou_none(resposta)
                    return ResultadoRepasse(Desfecho.ENTREGUE, resposta.status_code, (dados or {}).get("dados"))
                if resposta.status_code in (400, 404, 409):
                    logger.warning(
                        "PageFlow: %s recusado (HTTP %s): %s — resultado descartado",
                        caminho, resposta.status_code, resposta.text[:300],
                    )
                    return ResultadoRepasse(Desfecho.DESCARTADO, resposta.status_code, _json_ou_none(resposta))
                if resposta.status_code in (401, 403):
                    # Chave errada ou sem a permissão pcp_retorno: repetir não
                    # resolve, mas o resultado não pode se perder.
                    logger.error("PageFlow: %s negado (HTTP %s) — confira PAGEFLOW_API_KEY", caminho, resposta.status_code)
                    return ResultadoRepasse(Desfecho.NAO_ENTREGUE, resposta.status_code)
                logger.warning("PageFlow: %s HTTP %s, tentativa %s/%s", caminho, resposta.status_code, tentativa, self._tentativas)
            if tentativa < self._tentativas:
                self._dormir(min(2 ** tentativa, 30))
        return ResultadoRepasse(Desfecho.NAO_ENTREGUE, ultimo_status)

    def fechar(self) -> None:
        self._http.close()


def _json_ou_none(resposta: httpx.Response):
    try:
        return resposta.json()
    except ValueError:
        return None
