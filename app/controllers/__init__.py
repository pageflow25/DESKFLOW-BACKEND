"""Handlers da fila: um módulo por `tipo_codigo`.

Cada handler implementa o protocolo de `fila/registry.py` (`preparar`,
`interpretar`, `verificar`) e é a ÚNICA peça que conhece o formato do payload,
o endpoint do destino e o formato do resultado que o projetor do PageFlow lê.
O motor não importa nada daqui: a dependência é só pelo registry.

Fase 2a: os três tipos de cliente. Fase 3: planilha por linha, sincronização
paginada e importação de produto. **Fase 5: os três tipos de PCP** — envio de
orçamento, aprovação de proposta e download dos arquivos da OP.

`vendedor.listar_pagina` continua SEM handler de propósito: o endpoint de
vendedores do ERP não está documentado e precisa ser confirmado com a Bremen.
Tipo ativo sem handler não perde item — o motor devolve a linha a `pendente`
com atraso e registra o erro —, e inventar um caminho de API seria pior que a
espera.
"""

import logging

from .clientes.atualizar import HandlerClienteAtualizar
from .clientes.consultar import HandlerClienteConsultar
from .clientes.criar import HandlerClienteCriar
from .clientes.planilha import HandlerClientePlanilhaLinha
from .clientes.sincronizar_pagina import HandlerClienteSincronizarPagina
from .pcp.aprovacao_enviar import HandlerPcpAprovacaoEnviar
from .pcp.download_arquivos import HandlerPcpDownloadArquivos
from .pcp.orcamento_enviar import HandlerPcpOrcamentoEnviar
from .produtos.importar import HandlerProdutoImportar

logger = logging.getLogger(__name__)


def registrar_erp_wingraph(registry, erp, *, baixador=None, pasta_download: str = "") -> None:
    """Registra os handlers do destino `erp_wingraph` num registry.

    Recebe o registry em vez de criá-lo para que os testes possam montar um
    registry só com o que estão exercitando.

    `baixador` e `pasta_download` existem só para `pcp.download_arquivos`, que é
    o único handler que não fala com o ERP: ele escreve arquivo em disco. Sem os
    dois o handler NÃO é registrado — e isso é a decisão certa, não um descuido:
    um worker sem `DOWNLOAD_BASE_PATH` não tem onde publicar a pasta da OP, e
    tipo ativo sem handler não perde item (o motor devolve a linha a `pendente`
    com atraso e registra o erro), enquanto um handler registrado sem destino
    falharia item por item.
    """
    handlers = [
        HandlerClienteConsultar(erp),
        HandlerClienteCriar(erp),
        HandlerClienteAtualizar(erp),
        HandlerClientePlanilhaLinha(erp),
        HandlerClienteSincronizarPagina(erp),
        HandlerProdutoImportar(erp),
        HandlerPcpOrcamentoEnviar(erp),
        HandlerPcpAprovacaoEnviar(erp),
    ]
    if baixador is not None and pasta_download:
        handlers.append(HandlerPcpDownloadArquivos(baixador, pasta_download))
    else:
        logger.info(
            "Fila: %s sem handler neste worker (DOWNLOAD_BASE_PATH vazio ou baixador ausente)",
            HandlerPcpDownloadArquivos.tipo,
        )
    for handler in handlers:
        registry.registrar(handler.tipo, handler)


__all__ = [
    "HandlerClienteAtualizar",
    "HandlerClienteConsultar",
    "HandlerClienteCriar",
    "HandlerClientePlanilhaLinha",
    "HandlerClienteSincronizarPagina",
    "HandlerPcpAprovacaoEnviar",
    "HandlerPcpDownloadArquivos",
    "HandlerPcpOrcamentoEnviar",
    "HandlerProdutoImportar",
    "registrar_erp_wingraph",
]
