"""Monta as peças do DESKFLOW2.0 a partir da configuração."""

import logging
from dataclasses import dataclass
from typing import Optional

from ..integracoes.erp import ErpClient
from .config import Settings, get_settings
from .database import get_engine
from ..fila.catalogo import carregar_catalogo
from ..fila.escalonador import Escalonador
from ..fila.registry import registry_padrao
from ..servicos.pcp.download_arquivos import BaixadorArquivos

logger = logging.getLogger(__name__)


@dataclass
class Aplicacao:
    settings: Settings
    engine: object
    erp: ErpClient
    # None quando FILA_ATIVA=false ou quando o catálogo da fila ainda não
    # existe no banco: nesse caso o worker sobe sem ciclo nenhum, porque a
    # fila é o único caminho de processamento que existe.
    fila: Optional[Escalonador] = None

    def fechar(self) -> None:
        if self.fila is not None:
            self.fila.encerrar()
        self.erp.fechar()
        self.engine.dispose()


def _montar_fila(engine, settings: Settings, erp: ErpClient,
                 baixador: BaixadorArquivos) -> Optional[Escalonador]:
    """Catálogos resolvidos UMA vez, aqui — nada de número mágico no SQL nem de
    JOIN de status no caminho quente do claim."""
    if not settings.FILA_ATIVA:
        logger.info("Fila: FILA_ATIVA=false, escalonador não montado")
        return None
    try:
        with engine.connect() as conn:
            catalogo = carregar_catalogo(conn)
    except Exception:
        logger.exception("Fila: catálogo indisponível no banco; escalonador não montado")
        return None

    # Um cliente HTTP só para os downloads, um pool só.
    registry = registry_padrao(erp, baixador=baixador, pasta_download=settings.DOWNLOAD_BASE_PATH)
    escalonador = Escalonador(engine, catalogo, registry, settings)
    logger.info(
        "Fila: worker %s, pools sincrono=%s assincrono=%s, destino %s, %s tipo(s) no catálogo, "
        "handler(s) para %s",
        escalonador.worker_id, settings.FILA_POOL_SINCRONO, settings.FILA_POOL_ASSINCRONO,
        settings.FILA_DESTINO, len(catalogo.tipos), ", ".join(registry.codigos()) or "nenhum tipo",
    )
    return escalonador


def montar_aplicacao(settings: Settings | None = None) -> Aplicacao:
    settings = settings or get_settings()
    engine = get_engine()
    erp = ErpClient(settings)
    baixador = BaixadorArquivos(settings)
    return Aplicacao(
        settings=settings,
        engine=engine,
        erp=erp,
        fila=_montar_fila(engine, settings, erp, baixador),
    )
