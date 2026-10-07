"""Registry de handlers por `tipo_codigo`.

O motor não conhece nenhum handler: ele pede um ao registry pelo código do
tipo e conversa com ele pelo protocolo abaixo. Um tipo novo é um `INSERT` em
`fila_processamento_tipos` mais um `registrar()` aqui — nada de DDL e nada de
`if tipo == ...` dentro do motor.

Na Fase 1 o registry subiu vazio de propósito: com os 10 tipos semeados em
`ativo = false`, o claim nunca devolvia item nenhum. A Fase 2a registrou os três
tipos de cliente e **a Fase 3 acrescenta a planilha por linha, a sincronização
paginada e a importação de produto**; os demais continuam sem handler e sem
ativação, e só entram junto com a fase que os implementa.
"""

import logging
from typing import Any, Optional, Protocol, runtime_checkable

from .modelos import Desfecho, ItemReivindicado, Preparo

logger = logging.getLogger(__name__)


@runtime_checkable
class Handler(Protocol):
    """Protocolo da seção 10 do plano.

    `preparar` roda DENTRO de uma transação curta (recebe a conexão) e não faz
    I/O externa. `interpretar` e `verificar` recebem o contexto do worker.
    """

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        """Monta o corpo e devolve a chamada a ser feita. Sem I/O externa."""

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        """Classifica o retorno cru da chamada em um desfecho."""

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Resolve um item `incerto` consultando o destino por chave natural.
        `None` quando não há como decidir — o item continua incerto.

        Roda fora de transação (faz I/O externa), então `conn` chega `None`
        quando quem chama é o motor."""


class Registry:
    def __init__(self) -> None:
        self._handlers: dict = {}

    def registrar(self, tipo_codigo: str, handler: Handler) -> None:
        if tipo_codigo in self._handlers:
            raise ValueError(f"Handler duplicado para o tipo '{tipo_codigo}'")
        for metodo in ("preparar", "interpretar"):
            if not callable(getattr(handler, metodo, None)):
                raise TypeError(f"Handler de '{tipo_codigo}' não implementa {metodo}()")
        self._handlers[tipo_codigo] = handler
        logger.info("Fila: handler registrado para %s", tipo_codigo)

    def obter(self, tipo_codigo: str) -> Optional[Handler]:
        return self._handlers.get(tipo_codigo)

    def codigos(self) -> list:
        return sorted(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)


def registry_padrao(erp, *, baixador=None, pasta_download: str = "") -> Registry:
    """O registry do worker.

    O import é local para deixar claro o sentido da dependência: o motor (e este
    módulo, que ele importa) não conhece handler nenhum; é o registry, montado
    na borda da aplicação, que junta os dois. `erp` é o cliente do destino
    `erp_wingraph`, injetado para que o teste monte o registry com um transporte
    falso.

    `baixador` e `pasta_download` são a segunda borda, do único handler que
    escreve arquivo em disco em vez de falar com o ERP
    (`pcp.download_arquivos`); ver `handlers/registrar_erp_wingraph`.
    """
    from ..handlers import registrar_erp_wingraph

    registry = Registry()
    registrar_erp_wingraph(registry, erp, baixador=baixador, pasta_download=pasta_download)
    return registry
