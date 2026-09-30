"""Leitura da RESPOSTA do destino `erp_wingraph`, comum aos handlers.

Tudo aqui olha para o que CHEGOU do ERP:

- **leitura do envelope** (`data`, `metadata.pages`), que e o mesmo em
  `/api/v1/cliente` e `/api/v1/caracteristicasproduto`;
- **classificacao da resposta** nos tres baldes (definitiva, retentavel,
  incerta). A classificacao de transporte (`ErpIndisponivel` x
  `ResultadoIncerto`) continua sendo do `ErpClient`; o que se classifica aqui e
  a resposta que chegou inteira e diz "nao";
- **a consulta por documento**, que e o verificador de `cliente.criar` e
  `cliente.atualizar`.

O que olha para o payload que ENTROU saiu para `deskflow2/validadores/`:
`PayloadInvalido`, `preparo_invalido`, `inteiro_positivo`, `so_digitos` e
`documento_do_payload`. A fronteira e a direcao do dado — ver o docstring de lá.
"""

from typing import Any, Optional

import httpx

from ..integracoes.erp import ErroErp, ler_json, mensagem_de_erro, sucesso_erp
from ..fila.modelos import Desfecho
from ..validadores.comum import inteiro_positivo, so_digitos

# Status que o ERP devolve SEM ter processado a chamada — vale repetir mesmo em
# tipo não idempotente. O 503 não chega aqui: o `ErpClient` já o converte em
# `ErpIndisponivel` antes de devolver resposta.
STATUS_RETENTAVEIS = frozenset({408, 425, 429, 502, 503, 504})


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


def id_cliente_de(registro: Any) -> Optional[int]:
    if not isinstance(registro, dict):
        return None
    return inteiro_positivo(registro.get("id_cliente"))
