"""Fila de processamento única (PageFlow -> DESKFLOW -> destinos).

Camadas, na mesma separação que o pacote já usa:

- `catalogo`     — cache de `fila_processamento_status` e `_tipos` (codigo -> id
                   e codigo -> tipo), carregado uma vez no `montar_aplicacao()`;
- `repositorio`  — todo o SQL (claim, transições, eventos, reaper, heartbeat);
- `motor`        — o que fazer com um item: preparar, chamar, interpretar,
                   classificar a falha e agendar a próxima tentativa;
- `registry`     — handlers por `tipo_codigo`. O motor não conhece nenhum
                   handler: quem os implementa é `app/controllers/`, e o
                   registry é o único ponto em que os dois se encontram;
- `escalonador`  — dois pools (síncrono e assíncrono), dois despachantes,
                   reaper e heartbeat.

Regra do contrato: o DESKFLOW só escreve as tabelas `fila_*`. Nenhuma tabela de
domínio é tocada aqui.
"""

from .catalogo import Catalogo, carregar_catalogo
from .modelos import Desfecho, Estado, ItemReivindicado, Preparo
from .registry import Registry

__all__ = [
    "Catalogo",
    "carregar_catalogo",
    "Desfecho",
    "Estado",
    "ItemReivindicado",
    "Preparo",
    "Registry",
]
