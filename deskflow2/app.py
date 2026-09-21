"""Monta as peças do DESKFLOW2.0 a partir da configuração."""

from dataclasses import dataclass

from .clientes.erp import ErpClient
from .clientes.pageflow import PageflowClient
from .config import Settings, get_settings
from .db import get_engine
from .servicos.despacho_aprovacao import DespachoAprovacoes
from .servicos.despacho_orcamento import DespachoOrcamentos
from .servicos.download_arquivos import BaixadorArquivos, DownloadArquivos
from .servicos.reconciliacao import Reconciliacao
from .servicos.repasse import Repassador


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

    def fechar(self) -> None:
        self.erp.fechar()
        self.pageflow.fechar()
        self.engine.dispose()


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
    )
