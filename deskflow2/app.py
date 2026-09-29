"""Monta as peças do DESKFLOW2.0 a partir da configuração."""

import logging
from dataclasses import dataclass
from typing import Optional

from .clientes.erp import ErpClient
from .clientes.pageflow import PageflowClient
from .config import Settings, get_settings
from .db import get_engine
from .fila.catalogo import carregar_catalogo
from .fila.escalonador import Escalonador
from .fila.registry import registry_padrao
from .servicos.despacho_aprovacao import DespachoAprovacoes
from .servicos.despacho_orcamento import DespachoOrcamentos
from .servicos.download_arquivos import BaixadorArquivos, DownloadArquivos
from .servicos.reconciliacao import Reconciliacao
from .servicos.repasse import Repassador

logger = logging.getLogger(__name__)


@dataclass
class Aplicacao:
    settings: Settings
    engine: object
    erp: ErpClient
    pageflow: PageflowClient
    repassador: Repassador
    orcamentos: DespachoOrcamentos
    aprovacoes: DespachoAprovacoes
    downloads: DownloadArquivos
    reconciliacao: Reconciliacao
    # None quando FILA_ATIVA=false ou quando o catálogo da fila ainda não
    # existe no banco (Fase 0 não aplicada): os ciclos antigos seguem normais.
    fila: Optional[Escalonador] = None

    def fechar(self) -> None:
        if self.fila is not None:
            self.fila.encerrar()
        self.erp.fechar()
        self.pageflow.fechar()
        self.engine.dispose()


def _montar_fila(engine, settings: Settings, erp: ErpClient) -> Optional[Escalonador]:
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

    registry = registry_padrao(erp)
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
    pageflow = PageflowClient(settings)
    repassador = Repassador(pageflow, settings.DADOS_DIR)
    return Aplicacao(
        settings=settings,
        engine=engine,
        erp=erp,
        pageflow=pageflow,
        repassador=repassador,
        orcamentos=DespachoOrcamentos(engine, erp, repassador, settings),
        aprovacoes=DespachoAprovacoes(engine, erp, repassador, settings),
        downloads=DownloadArquivos(engine, BaixadorArquivos(settings), repassador, settings),
        reconciliacao=Reconciliacao(engine, erp, repassador, settings),
        fila=_montar_fila(engine, settings, erp),
    )
