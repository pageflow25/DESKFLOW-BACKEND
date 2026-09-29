"""Peças comuns aos handlers do destino `erp_wingraph`.

Três assuntos moram aqui porque os três handlers de cliente os compartilham e
nenhum deles pertence ao motor:

- **classificação da resposta** nos três baldes da seção 5 do plano (definitiva,
  retentável, incerta). A classificação de transporte (`ErpIndisponivel` ×
  `ResultadoIncerto`) continua sendo do `ErpClient`; o que se classifica aqui é
  a resposta que chegou inteira e diz "não";
- **payload inválido**, que é falha DEFINITIVA e não pode virar exceção: exceção
  no `preparar` é retentável por construção no motor, e CNPJ faltando não fica
  melhor na terceira tentativa;
- **a consulta por documento**, que é o verificador de `cliente.criar` e
  `cliente.atualizar`.
"""

from typing import Any, Optional

import httpx

from ..clientes.erp import ErroErp, ler_json, mensagem_de_erro, sucesso_erp
from ..fila.modelos import Desfecho

# Status que o ERP devolve SEM ter processado a chamada — vale repetir mesmo em
# tipo não idempotente. O 503 não chega aqui: o `ErpClient` já o converte em
# `ErpIndisponivel` antes de devolver resposta.
STATUS_RETENTAVEIS = frozenset({408, 425, 429, 502, 503, 504})


class PayloadInvalido:
    """Devolvida por `preparar()` NO LUGAR da chamada, quando o item não tem o
    mínimo para falar com o ERP.

    Não é exceção de propósito: o motor trata exceção no preparo como
    retentável, e payload inválido não melhora na tentativa seguinte.
    `interpretar()` a reconhece e devolve falha definitiva, sem gastar tentativa
    e sem nenhuma chamada sair.
    """

    def __init__(self, mensagem: str, codigo: str = "PAYLOAD_INVALIDO"):
        self.mensagem = mensagem
        self.codigo = codigo

    def desfecho(self) -> Desfecho:
        return Desfecho.falhou(self.mensagem, self.codigo)


def so_digitos(valor: Any) -> str:
    """Forma canônica do documento, a mesma que o PageFlow usa nas chaves de
    bloqueio e de idempotência (`services/Bremen/bremenClienteFilaService.js`)."""
    return "".join(caractere for caractere in str(valor or "") if caractere.isdigit())


def documento_do_payload(payload: dict) -> str:
    """CNPJ ou CPF, na ordem em que o PageFlow os escolhe. O produtor já manda
    `documento` pronto; os outros dois caminhos existem para o item montado à
    mão pela tela de reprocesso."""
    payload = payload or {}
    cliente = payload.get("cliente") or {}
    for bruto in (payload.get("documento"), cliente.get("cnpj"), cliente.get("cpf"),
                  payload.get("cnpj"), payload.get("cpf"), payload.get("cpfcnpj")):
        documento = so_digitos(bruto)
        if documento:
            return documento
    return ""


def envelope_json(resposta: httpx.Response) -> Optional[dict]:
    dados = ler_json(resposta)
    return dados if isinstance(dados, dict) else None


def registros_do_envelope(dados: Optional[dict]) -> list:
    """`data` de qualquer envelope do Wingraph, sempre como lista.

    A API devolve lista nas buscas e já devolveu objeto único em resposta de
    escrita — os dois viram lista aqui. Serve a `/api/v1/cliente` e a
    `/api/v1/caracteristicasproduto`, que compartilham o mesmo envelope.
    """
    if not isinstance(dados, dict):
        return []
    interno = dados.get("data")
    if isinstance(interno, list):
        return [linha for linha in interno if isinstance(linha, dict)]
    if isinstance(interno, dict):
        return [interno]
    return []


def clientes_do_envelope(dados: Optional[dict]) -> list:
    """`data` do GET /api/v1/cliente. Nome de domínio para o mesmo envelope."""
    return registros_do_envelope(dados)


def total_paginas_de(dados: Optional[dict]) -> Optional[int]:
    """`metadata.pages` do envelope paginado.

    `None` quando o ERP não informou — quem depende disso (a sincronização
    paginada) precisa tratar a ausência, nunca presumir uma página só.
    """
    if not isinstance(dados, dict):
        return None
    metadata = dados.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return inteiro_positivo(metadata.get("pages"))


def classificar_falha(resposta: httpx.Response, dados: Optional[dict], *, mutacao: bool) -> Desfecho:
    """A resposta chegou inteira e não é sucesso. Em qual balde ela cai?

    - `STATUS_RETENTAVEIS`: o ERP não processou — retentável mesmo em escrita;
    - outros 5xx: o ERP recebeu e quebrou no meio. Em leitura repetir é grátis;
      em escrita pode ter gravado, então é INCERTO e quem decide é `verificar`;
    - 4xx e `success: false`: o destino processou e RECUSOU. Falha definitiva,
      que por desenho não consome o orçamento de tentativas.
    """
    codigo = resposta.status_code
    mensagem = mensagem_de_erro(dados, resposta)
    if codigo in STATUS_RETENTAVEIS:
        return Desfecho.retentar(f"ERP HTTP {codigo}: {mensagem}", f"ERP_HTTP_{codigo}")
    if codigo >= 500:
        if mutacao:
            return Desfecho.incerto(
                f"ERP HTTP {codigo} depois de receber a chamada: {mensagem}", f"ERP_HTTP_{codigo}")
        return Desfecho.retentar(f"ERP HTTP {codigo}: {mensagem}", f"ERP_HTTP_{codigo}")
    return Desfecho.falhou(f"ERP recusou (HTTP {codigo}): {mensagem}", f"ERP_HTTP_{codigo}")


def consultar_por_documento(erp, documento: str) -> Optional[dict]:
    """Busca UM cliente pelo documento. É o verificador de `cliente.criar` e
    `cliente.atualizar`.

    Devolve o registro, `None` quando o ERP respondeu que não existe, e levanta
    `ErroErp` quando a própria consulta não pôde ser concluída — nesse caso
    quem chama mantém o item `incerto`, porque uma consulta que falhou não é
    prova de ausência (é exatamente o falso negativo silencioso do
    `verificarClienteExistente` legado, que engole o erro e devolve
    `existe: false`).
    """
    resposta = erp.listar_clientes(cpfcnpj=documento)
    dados = envelope_json(resposta)
    if resposta.status_code >= 400 or not sucesso_erp(dados):
        raise ErroErp(
            f"consulta por documento não concluída (HTTP {resposta.status_code}): "
            f"{mensagem_de_erro(dados, resposta)}"
        )
    for registro in clientes_do_envelope(dados):
        if so_digitos(registro.get("cnpj")) == documento or so_digitos(registro.get("cpf")) == documento:
            return registro
    # Sem filtro batendo: o ERP respondeu a busca e não devolveu o documento.
    return None


def inteiro_positivo(valor: Any) -> Optional[int]:
    """Inteiro > 0, ou `None`. Aceita o número como texto, que é como ele chega
    do JSONB do payload em boa parte dos produtores."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        try:
            numero = int(str(valor).strip())
        except (TypeError, ValueError):
            return None
    return numero if numero > 0 else None


def id_cliente_de(registro: Any) -> Optional[int]:
    if not isinstance(registro, dict):
        return None
    return inteiro_positivo(registro.get("id_cliente"))
