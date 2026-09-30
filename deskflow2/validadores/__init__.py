"""Validacao de PAYLOAD dos itens da fila, um modulo por dominio.

Por que este pacote existe separado dos handlers: antes, a resposta para "este
payload e aceitavel?" estava espalhada pelo `preparar()` de nove handlers, cada
um escrevendo a propria mensagem inline. Nao havia um lugar onde as regras de um
dominio pudessem ser lidas de uma vez, e por isso tambem nao havia como notar que
duas delas discordavam.

A fronteira e a direcao do dado:

- **aqui** fica o que se pergunta ao payload que ENTROU, antes de qualquer
  chamada sair. Funcao pura, sem I/O, sem rede, sem banco;
- em `handlers/comum.py` fica o que se pergunta a resposta que CHEGOU: leitura de
  envelope do Wingraph, classificacao de falha nos tres baldes, consulta de
  verificacao.

Todo validador devolve `PayloadInvalido` ou `None`, nunca levanta excecao. A
diferenca importa: excecao no `preparar()` e RETENTAVEL por construcao no motor,
e payload invalido nao melhora na terceira tentativa — ele e falha definitiva e
nao deve gastar o orcamento de tentativas do item.

Mesmo contrato dos `validators/` do BACKEND_PAGEFLOW (por exemplo
`services/Pcp/validators/pcpEnfileiramentoValidator.js`), que sao as regras de
recusa do outro lado da mesma fila.
"""

from . import clientes, pcp, produtos
from .comum import PayloadInvalido, inteiro_positivo, preparo_invalido, so_digitos

__all__ = [
    "PayloadInvalido",
    "clientes",
    "inteiro_positivo",
    "pcp",
    "preparo_invalido",
    "produtos",
    "so_digitos",
]
