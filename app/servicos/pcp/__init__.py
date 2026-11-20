"""Servicos do dominio PCP.

Os tres modulos daqui eram a raiz de `servicos/` e desceram um nivel na
reorganizacao, por um motivo factual: nenhum deles e usado fora do PCP. O
antigo `comum.py` (hoje `envio.py`), em particular, prometia ser comum a todos
os dominios e era comum so aos dois handlers de ESCRITA do PCP (orcamento e
aprovacao).

Deixar isso explicito na pasta tem um efeito pratico: o dia em que aparecer um
servico de outro dominio, ele nasce ao lado de `pcp/`, e nao misturado com o que
ja existe.
"""
