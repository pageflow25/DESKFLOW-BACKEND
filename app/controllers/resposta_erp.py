"""Leitura da RESPOSTA do destino `erp_wingraph`, comum aos controllers.

Tudo aqui olha para o que CHEGOU do ERP:

- **classificacao da resposta** nos tres baldes (definitiva, retentavel,
  incerta). A classificacao de transporte (`ErpIndisponivel` x
  `ResultadoIncerto`) continua sendo do `ErpClient`; o que se classifica aqui e
  a resposta que chegou inteira e diz "nao".

Era `handlers/comum.py`. A consulta por documento, que e regra de negocio de
clientes (o verificador de `cliente.criar` e `cliente.atualizar`), saiu para
`servicos/clientes/verificacao.py`; a leitura do envelope (`data`,
`metadata.pages`), que nao depende da fila, foi para `integracoes/erp.py`.

O que olha para o payload que ENTROU fica no `validators/` de cada modulo
(`controllers/<modulo>/validators/`), com a recusa em `payload_invalido.py`. A
fronteira e a direcao do dado — ver o docstring de `payload_invalido.py`.
"""

from typing import Optional

import httpx

from ..fila.modelos import Desfecho
from ..integracoes.erp import mensagem_de_erro

# Status que o ERP devolve SEM ter processado a chamada — vale repetir mesmo em
# tipo não idempotente. O 503 não chega aqui: o `ErpClient` já o converte em
# `ErpIndisponivel` antes de devolver resposta.
STATUS_RETENTAVEIS = frozenset({408, 425, 429, 502, 503, 504})


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
