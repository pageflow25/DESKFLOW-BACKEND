"""Cliente HTTP da API do ERP Wingraph (servida pela Bremen Sistemas).

- Login em POST /api/v1/auth; o token vale ~2h e não tem refresh, então é
  renovado antes de vencer e uma vez ao receber 401.
- Manda o token nos dois headers que a documentação cita (`token` e
  `Authorization: Bearer`).
- Retry só quando é certo que o ERP NÃO processou a chamada: 503 e erro de
  conexão. Um POST que estoura o tempo de leitura (ou devolve 2xx ilegível)
  pode ter criado o orçamento/aprovação — o ERP não é idempotente —, então
  vira `ResultadoIncerto` e não é repetido.

Sob a fila (`modo_fila()`), duas coisas mudam e SÓ ali:

- o semáforo passa a cobrar reserva de classe: a classe assíncrona enxerga
  (ERP_MAX_CONEXOES - ERP_CONEXOES_RESERVADAS_SINCRONO) conexões e a síncrona
  enxerga todas. Sem isso os pools separados do escalonador seriam ilusórios,
  porque disputariam o mesmo cliente HTTP;
- a espera do 503 sai da thread: `ErpIndisponivel` na primeira ocorrência, e
  quem devolve a linha para `pendente` com `disponivel_em` futuro é o motor.

Fora do `modo_fila()` — que é o caso dos quatro ciclos antigos — nada disso
entra em cena e o comportamento é exatamente o de antes.
"""

import contextlib
import contextvars
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

PADRAO_MAX_CONEXOES = 6
PADRAO_CONEXOES_RESERVADAS_SINCRONO = 2

CAMINHO_CLIENTE = "/api/v1/cliente"
CAMINHO_CARACTERISTICAS_PRODUTO = "/api/v1/caracteristicasproduto"
CAMINHO_ORCAMENTO = "/api/v1/orcamento"
CAMINHO_PROPOSTA = "/api/v1/proposta"
CAMINHO_PROPOSTA_APROVAR = "/api/v1/proposta/aprovar"

# `origem` do GET de características: 2 = modelo de produto (o que corresponde
# ao `id_produto` do PageFlow); 1 = item de estoque, cadastro diferente no ERP e
# não usado aqui.
ORIGEM_MODELO_DE_PRODUTO = 2


@dataclass(frozen=True)
class ContextoFila:
    classe: str


# Nulo fora da fila: os ciclos antigos nunca entram no `modo_fila()`.
_contexto_fila: contextvars.ContextVar = contextvars.ContextVar("erp_contexto_fila", default=None)


@contextlib.contextmanager
def modo_fila(classe: str):
    """Marca a execução atual como item da fila, da classe informada."""
    token = _contexto_fila.set(ContextoFila(classe=classe))
    try:
        yield
    finally:
        _contexto_fila.reset(token)


def contexto_fila() -> Optional[ContextoFila]:
    return _contexto_fila.get()


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


def extrair_id_requisicao(corpo: Any) -> Optional[int]:
    """`id_requisicao` do ack de uma chamada assíncrona, em qualquer das duas
    posições em que o Wingraph já o devolveu (`data.id_requisicao` e a raiz).

    Mora aqui, com os outros leitores de envelope, porque os dois caminhos que o
    consultam são independentes: o repasse do ciclo antigo e os handlers de PCP
    da fila.
    """
    if not isinstance(corpo, dict):
        return None
    dados = corpo.get("data") if isinstance(corpo.get("data"), dict) else {}
    valor = dados.get("id_requisicao", corpo.get("id_requisicao"))
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


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
        self._maximo = max(1, int(getattr(settings, "ERP_MAX_CONEXOES", PADRAO_MAX_CONEXOES)))
        reserva = int(getattr(settings, "ERP_CONEXOES_RESERVADAS_SINCRONO", PADRAO_CONEXOES_RESERVADAS_SINCRONO))
        self._reserva_sincrono = min(max(0, reserva), self._maximo - 1)
        # Limites explícitos: o pool default do httpx funcionava por acidente.
        self._http = http or httpx.Client(
            timeout=settings.ERP_TIMEOUT,
            limits=httpx.Limits(
                max_connections=self._maximo,
                max_keepalive_connections=self._maximo,
                keepalive_expiry=30.0,
            ),
        )
        self._relogio = relogio
        self._dormir = dormir
        self._token: Optional[str] = None
        self._token_obtido_em = 0.0
        self._geracao_token = 0
        self._lock = threading.Lock()
        # Reserva de classe: a assíncrona tem de segurar OS DOIS semáforos, a
        # síncrona só o total. Logo a assíncrona nunca passa de
        # (máximo - reserva) e sempre sobra conexão para o síncrono.
        self._vagas_total = threading.BoundedSemaphore(self._maximo)
        self._vagas_assincrono = threading.BoundedSemaphore(max(1, self._maximo - self._reserva_sincrono))

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
        self._geracao_token += 1
        logger.info("ERP: token renovado")
        return self._token

    def garantir_login(self) -> None:
        """Faz login se o token estiver vencido. Chamado no início de cada
        ciclo, antes de qualquer claim: com o ERP fora ou a senha errada, nada
        sai da fila.

        Lazy, com dupla verificação: o caminho comum (token válido) não toca no
        lock. Antes, com o lock tomado em TODA chamada, N threads serializavam
        aqui só para descobrir que o token estava bom."""
        if not self._token_vencido():
            return
        with self._lock:
            if self._token_vencido():
                self._autenticar()

    def _renovar_token(self, geracao_vista: Optional[int] = None) -> None:
        """Renovação depois de um 401. `geracao_vista` é a geração do token que
        o chamador usou: se outra thread já renovou no meio tempo, esta não
        renova de novo — é a guarda contra duas threads em 401 simultâneo
        fazerem dois logins (e um invalidar o token do outro)."""
        with self._lock:
            if geracao_vista is not None and geracao_vista != self._geracao_token:
                return
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

    @contextlib.contextmanager
    def _vaga(self):
        """Uma conexão ao ERP. Fora da fila não há espera nenhuma: o semáforo
        total tem `ERP_MAX_CONEXOES` vagas e os ciclos antigos são
        single-thread."""
        contexto = _contexto_fila.get()
        assincrono = contexto is not None and contexto.classe == "assincrono"
        if assincrono:
            self._vagas_assincrono.acquire()
        self._vagas_total.acquire()
        try:
            yield
        finally:
            self._vagas_total.release()
            if assincrono:
                self._vagas_assincrono.release()

    def _enviar(self, metodo: str, caminho: str, **kwargs) -> httpx.Response:
        self.garantir_login()
        geracao = self._geracao_token
        url = f"{self._base}{caminho}"
        with self._vaga():
            resposta = self._http.request(metodo, url, headers=self._headers(), **kwargs)
            if resposta.status_code == 401:
                logger.warning("ERP: 401 em %s %s, renovando o token", metodo, caminho)
                self._renovar_token(geracao)
                resposta = self._http.request(metodo, url, headers=self._headers(), **kwargs)
        return resposta

    def _janela_de_espera(self) -> int:
        """Quanto tempo o cliente pode insistir num erro retentável.

        Sob a fila é ZERO: nenhuma thread dorme esperando o ERP voltar. A
        espera vira `disponivel_em` na linha, com backoff e jitter, e o worker
        segue com o próximo item. Fora da fila continua valendo
        `ERP_503_MAX_WAIT_SECONDS`, como sempre."""
        if _contexto_fila.get() is not None:
            return 0
        return max(0, self._settings.ERP_503_MAX_WAIT_SECONDS)

    def _com_retry(self, metodo: str, caminho: str, idempotente: bool, **kwargs) -> httpx.Response:
        janela = self._janela_de_espera()
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

    def _mutacao(self, metodo: str, caminho: str, corpo: dict) -> httpx.Response:
        """POST e PATCH têm exatamente a mesma regra: a API não é idempotente,
        então uma resposta 2xx ilegível pode ser uma escrita que aconteceu."""
        resposta = self._com_retry(metodo, caminho, idempotente=False, json=corpo)
        if 200 <= resposta.status_code < 300 and ler_json(resposta) is None:
            raise ResultadoIncerto(f"resposta ilegível do ERP em {caminho} (HTTP {resposta.status_code})")
        return resposta

    def post(self, caminho: str, corpo: dict) -> httpx.Response:
        return self._mutacao("POST", caminho, corpo)

    def patch(self, caminho: str, corpo: dict) -> httpx.Response:
        return self._mutacao("PATCH", caminho, corpo)

    # --- Cliente (/api/v1/cliente) ----------------------------------------------
    #
    # Contrato espelhado de `services/Integracoes/wingraphErpClient.js`, que é o
    # que fala com esse endpoint em produção hoje e será desligado: GET com os
    # filtros soltos em querystring; POST e PATCH com o corpo dentro do envelope
    # `{identifier, data}`; e o `id_cliente` viajando DENTRO de `data` no PATCH.

    @property
    def identifier(self) -> str:
        """O `identifier` que todo corpo de escrita do Wingraph carrega.

        Público porque o corpo do orçamento do PCP não é montado aqui: ele sai
        pronto do SQL (`servicos/payload.py`), e o handler só precisa saber qual
        identifier prefixar — sem alcançar `_settings` de fora."""
        return self._settings.ERP_IDENTIFIER

    def _envelope(self, dados: dict) -> dict:
        return {"identifier": self.identifier, "data": dados}

    def listar_clientes(self, id_cliente: Optional[int] = None, cpfcnpj: Optional[str] = None,
                        email: Optional[str] = None, page: Optional[int] = None) -> httpx.Response:
        """GET /api/v1/cliente. Todos os filtros são opcionais; sem nenhum, a
        API pagina o cadastro inteiro (`metadata.pages`)."""
        params: dict = {}
        if id_cliente is not None:
            params["id"] = id_cliente
        if cpfcnpj:
            params["cpfcnpj"] = cpfcnpj
        if email:
            params["email"] = email
        if page is not None:
            params["page"] = page
        return self.get(CAMINHO_CLIENTE, params or None)

    def criar_cliente(self, cliente: dict) -> httpx.Response:
        """POST /api/v1/cliente. Escrita NÃO idempotente."""
        return self.post(CAMINHO_CLIENTE, self._envelope(cliente))

    def atualizar_cliente(self, cliente: dict) -> httpx.Response:
        """PATCH /api/v1/cliente. O corpo precisa ser a estrutura COMPLETA, com
        `id_cliente` e com os ids de contato/endereço: omitir um id faz o ERP
        CRIAR a linha em vez de editá-la, duplicando os registros a cada
        salvamento."""
        return self.patch(CAMINHO_CLIENTE, self._envelope(cliente))

    # --- Produto (/api/v1/caracteristicasproduto) --------------------------------

    def buscar_caracteristicas_produto(
        self, id_produto: int, origem: int = ORIGEM_MODELO_DE_PRODUTO
    ) -> httpx.Response:
        """GET /api/v1/caracteristicasproduto — item, componentes, perguntas
        (gerais e por componente) e opções de resposta de um produto.

        Contrato espelhado de `buscarCaracteristicasProduto` em
        `services/Integracoes/wingraphErpClient.js`: os dois filtros vão soltos
        na querystring (`id` e `origem`), sem envelope. Leitura, logo
        idempotente — repetir é grátis."""
        return self.get(CAMINHO_CARACTERISTICAS_PRODUTO, {"id": id_produto, "origem": origem})

    # --- PCP (/api/v1/orcamento e /api/v1/proposta) -------------------------------
    #
    # Os três caminhos que os handlers de PCP usam. O corpo chega PRONTO (o do
    # orçamento vem do SQL, o da aprovação é montado pelo handler), então aqui
    # não há envelope a construir: o valor destes métodos é manter o caminho da
    # API num lugar só, como já acontece com cliente e produto.

    def enviar_orcamento(self, corpo: dict) -> httpx.Response:
        """POST /api/v1/orcamento. Escrita NÃO idempotente: um orçamento
        duplicado no ERP só sai de lá à mão."""
        return self.post(CAMINHO_ORCAMENTO, corpo)

    def aprovar_proposta(self, corpo: dict) -> httpx.Response:
        """POST /api/v1/proposta/aprovar. Escrita NÃO idempotente: aprovar duas
        vezes gera OP/PV duplicados na produção."""
        return self.post(CAMINHO_PROPOSTA_APROVAR, corpo)

    def consultar_proposta(self, id_orcamento: int) -> httpx.Response:
        """GET /api/v1/proposta da ÚLTIMA proposta do orçamento.

        É a consulta prévia da aprovação: leitura, portanto idempotente e sem
        efeito nenhum se repetida."""
        return self.get(CAMINHO_PROPOSTA, {"id_orcamento": id_orcamento, "apenas_ultima": "true"})

    def fechar(self) -> None:
        self._http.close()
