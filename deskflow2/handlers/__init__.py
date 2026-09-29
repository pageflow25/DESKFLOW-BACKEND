"""Handlers da fila: um módulo por `tipo_codigo`.

Cada handler implementa o protocolo de `fila/registry.py` (`preparar`,
`interpretar`, `verificar`) e é a ÚNICA peça que conhece o formato do payload,
o endpoint do destino e o formato do resultado que o projetor do PageFlow lê.
O motor não importa nada daqui: a dependência é só pelo registry.

Fase 2a: os três tipos de cliente. Fase 3: planilha por linha, sincronização
paginada e importação de produto.

`vendedor.listar_pagina` continua SEM handler de propósito: o endpoint de
vendedores do ERP não está documentado e precisa ser confirmado com a Bremen.
Tipo ativo sem handler não perde item — o motor devolve a linha a `pendente`
com atraso e registra o erro —, e inventar um caminho de API seria pior que a
espera.
"""

from .cliente_atualizar import HandlerClienteAtualizar
from .cliente_consultar import HandlerClienteConsultar
from .cliente_criar import HandlerClienteCriar
from .cliente_planilha import HandlerClientePlanilhaLinha
from .cliente_sincronizar_pagina import HandlerClienteSincronizarPagina
from .produto_importar import HandlerProdutoImportar


def registrar_erp_wingraph(registry, erp) -> None:
    """Registra os handlers do destino `erp_wingraph` num registry.

    Recebe o registry em vez de criá-lo para que os testes possam montar um
    registry só com o que estão exercitando.
    """
    handlers = (
        HandlerClienteConsultar(erp),
        HandlerClienteCriar(erp),
        HandlerClienteAtualizar(erp),
        HandlerClientePlanilhaLinha(erp),
        HandlerClienteSincronizarPagina(erp),
        HandlerProdutoImportar(erp),
    )
    for handler in handlers:
        registry.registrar(handler.tipo, handler)


__all__ = [
    "HandlerClienteAtualizar",
    "HandlerClienteConsultar",
    "HandlerClienteCriar",
    "HandlerClientePlanilhaLinha",
    "HandlerClienteSincronizarPagina",
    "HandlerProdutoImportar",
    "registrar_erp_wingraph",
]
