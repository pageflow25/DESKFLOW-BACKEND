"""Cliente HTTP da API do ERP Wingraph (servida pela Bremen Sistemas).

- Login em POST /api/v1/auth; o token vale ~2h e não tem refresh, então é
  renovado antes de vencer e uma vez ao receber 401.
- Manda o token nos dois headers que a documentação cita (`token` e
  `Authorization: Bearer`).
- Retry só quando é certo que o ERP NÃO processou a chamada: 503 e erro de
  conexão. Um POST que estoura o tempo de leitura (ou devolve 2xx ilegível)
  pode ter criado o orçamento/aprovação — o ERP não é idempotente —, então
  vira `ResultadoIncerto` e não é repetido.
"""

import logging
import threading
import time
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class ErroErp(Exception):
    """Falha ao falar com o ERP."""


class ErpIndisponivel(ErroErp):
    """O ERP não processou a chamada (503 ou sem conexão até o fim da janela)."""


class ResultadoIncerto(ErroErp):
    """O ERP pode ter processado a chamada, mas a resposta não chegou."""


class FalhaLogin(ErroErp):
    """Login no ERP recusado ou impossível."""


def ler_json(resposta: httpx.Response) -> Optional[Any]:
    try:
        return resposta.json()
    except ValueError:
        return None


def mensagem_de_erro(corpo: Any, resposta: Optional[httpx.Response] = None) -> str:
    """`message` + `data.error` do envelope de erro do Wingraph."""
    partes = []
    if isinstance(corpo, dict):
        if corpo.get("message"):
            partes.append(str(corpo["message"]).strip())
        dados = corpo.get("data")
        if isinstance(dados, dict) and dados.get("error"):
            partes.append(str(dados["error"]).strip())
    if partes:
        return " - ".join(partes)
    if resposta is not None:
        return (resposta.text or "").strip()[:500] or f"HTTP {resposta.status_code}"
    return "Resposta vazia do ERP"


def sucesso_erp(corpo: Any) -> bool:
    """A API grafa a chave como `success` e também `sucess`."""
    if not isinstance(corpo, dict):
        return False
    valor = corpo.get("success", corpo.get("sucess"))
    codigo = corpo.get("code")
    return valor is True and not (isinstance(codigo, int) and codigo >= 400)


class ErpClient:
    def __init__(
        self,
        settings,
        http: Optional[httpx.Client] = None,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self._settings = settings
        self._base = settings.ERP_BASE_URL
        self._http = http or httpx.Client(timeout=settings.ERP_TIMEOUT)
        self._relogio = relogio
        self._dormir = dormir
        self._token: Optional[str] = None
        self._token_obtido_em = 0.0
        self._lock = threading.Lock()

    # --- Token -----------------------------------------------------------------

    def _token_vencido(self) -> bool:
        if not self._token:
            return True
        vida = self._settings.ERP_TOKEN_VIDA_SEGUNDOS - self._settings.ERP_TOKEN_MARGEM_SEGUNDOS
        return self._relogio() - self._token_obtido_em >= vida

    def _autenticar(self) -> str:
        corpo = {
            "identifier": self._settings.ERP_IDENTIFIER,
            "data": {"user": self._settings.ERP_USER, "password": self._settings.ERP_PASSWORD},
        }
        try:
            resposta = self._http.post(f"{self._base}/api/v1/auth", json=corpo, timeout=30)
        except httpx.HTTPError as exc:
            raise FalhaLogin(f"Sem conexão com o ERP para login: {exc}") from exc

        dados = ler_json(resposta)
        token = None
        if isinstance(dados, dict):
            interno = dados.get("data")
            token = dados.get("token") or (interno.get("token") if isinstance(interno, dict) else None)
        if resposta.status_code >= 400 or not token:
            raise FalhaLogin(f"Login no ERP recusado (HTTP {resposta.status_code}): {mensagem_de_erro(dados, resposta)}")

        self._token = token.removeprefix("Bearer ").strip()
        self._token_obtido_em = self._relogio()
        logger.info("ERP: token renovado")
        return self._token

    def garantir_login(self) -> None:
        """Faz login se o token estiver vencido. Chamado no início de cada
        ciclo, antes de qualquer claim: com o ERP fora ou a senha errada, nada
        sai da fila."""
        with self._lock:
            if self._token_vencido():
                self._autenticar()

    def _renovar_token(self) -> None:
        with self._lock:
            self._token = None
            self._autenticar()

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "token": self._token or "",
            "Authorization": f"Bearer {self._token or ''}",
        }

    # --- Chamadas ---------------------------------------------------------------

    def _enviar(self, metodo: str, caminho: str, **kwargs) -> httpx.Response:
        self.garantir_login()
        url = f"{self._base}{caminho}"
        resposta = self._http.request(metodo, url, headers=self._headers(), **kwargs)
        if resposta.status_code == 401:
            logger.warning("ERP: 401 em %s %s, renovando o token", metodo, caminho)
            self._renovar_token()
            resposta = self._http.request(metodo, url, headers=self._headers(), **kwargs)
        return resposta

    def _com_retry(self, metodo: str, caminho: str, idempotente: bool, **kwargs) -> httpx.Response:
        janela = max(0, self._settings.ERP_503_MAX_WAIT_SECONDS)
        base = max(1, self._settings.ERP_503_RETRY_BASE_SECONDS)
        teto = max(1, self._settings.ERP_503_RETRY_MAX_INTERVAL_SECONDS)
        inicio = self._relogio()
        tentativa = 0
        while True:
            tentativa += 1
            motivo = None
            try:
                resposta = self._enviar(metodo, caminho, **kwargs)
                if resposta.status_code != 503:
                    return resposta
                motivo = "HTTP 503"
            except httpx.ConnectError as exc:
                motivo = f"sem conexão ({exc})"
            except httpx.TimeoutException as exc:
                # ConnectTimeout: o pedido nem saiu. Os demais (leitura/escrita):
                # num POST, o ERP pode ter recebido e processado.
                if not idempotente and not isinstance(exc, httpx.ConnectTimeout):
                    raise ResultadoIncerto(f"tempo esgotado esperando o ERP em {caminho}") from exc
                motivo = f"tempo esgotado ({type(exc).__name__})"
            except FalhaLogin:
                raise
            except httpx.HTTPError as exc:
                if not idempotente:
                    raise ResultadoIncerto(f"falha de comunicação em {caminho}: {exc}") from exc
                motivo = f"falha de comunicação ({exc})"

            restante = janela - (self._relogio() - inicio)
            if restante <= 0:
                raise ErpIndisponivel(f"{metodo} {caminho}: {motivo} por {janela}s")
            espera = min(base * tentativa, teto, restante)
            logger.warning("ERP: %s %s -> %s; nova tentativa em %.0fs", metodo, caminho, motivo, espera)
            self._dormir(espera)

    def get(self, caminho: str, params: Optional[dict] = None) -> httpx.Response:
        return self._com_retry("GET", caminho, idempotente=True, params=params)

    def post(self, caminho: str, corpo: dict) -> httpx.Response:
        resposta = self._com_retry("POST", caminho, idempotente=False, json=corpo)
        if 200 <= resposta.status_code < 300 and ler_json(resposta) is None:
            raise ResultadoIncerto(f"resposta ilegível do ERP em {caminho} (HTTP {resposta.status_code})")
        return resposta

    def fechar(self) -> None:
        self._http.close()
