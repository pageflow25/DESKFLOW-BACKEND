"""Dublês compartilhados pelos testes (sem banco nem rede)."""

from contextlib import contextmanager
from types import SimpleNamespace


def configuracao(**sobrescritas):
    padrao = dict(
        ERP_BASE_URL="https://erp.teste",
        ERP_USER="u",
        ERP_PASSWORD="p",
        ERP_IDENTIFIER="PageFlow",
        ERP_TIMEOUT=5,
        ERP_TOKEN_VIDA_SEGUNDOS=7200,
        ERP_TOKEN_MARGEM_SEGUNDOS=300,
        ERP_503_MAX_WAIT_SECONDS=60,
        ERP_503_RETRY_BASE_SECONDS=5,
        ERP_503_RETRY_MAX_INTERVAL_SECONDS=30,
        PAGEFLOW_API_URL="https://pageflow.teste",
        PAGEFLOW_API_KEY="pk_test_x",
        PAGEFLOW_TIMEOUT=5,
        PAGEFLOW_TENTATIVAS=3,
        PCP_MODO_ENVIO="assincrono",
        PCP_ENVIO_LOTE_MAXIMO=20,
        PCP_PAUSA_ENTRE_ENVIOS_SEGUNDOS=0,
        PCP_RECONCILIACAO_MINUTOS=30,
        PCP_RECONCILIACAO_LIMITE_HORAS=6,
        PCP_DOWNLOAD_REINICIO_MINUTOS=60,
        BLOB_READ_WRITE_TOKEN="blob-token",
        DOWNLOAD_BASE_PATH="",
        DOWNLOAD_TIMEOUT=5,
        DOWNLOAD_TENTATIVAS=2,
    )
    padrao.update(sobrescritas)
    return SimpleNamespace(**padrao)


class MotorFalso:
    """Engine que só abre "transações" vazias: os repositórios são trocados
    por dublês nos testes que o usam."""

    def __init__(self):
        self.transacoes = 0

    @contextmanager
    def begin(self):
        self.transacoes += 1
        yield object()

    connect = begin


class RepassadorFalso:
    def __init__(self):
        self.enviados = []
        self.guardados = 0

    def quantos_pendentes(self):
        return self.guardados

    def orcamento(self, registro_id, corpo):
        self.enviados.append(("orcamentos", registro_id, corpo))
        return SimpleNamespace(desfecho=SimpleNamespace(value="entregue"))

    def aprovacao(self, registro_id, corpo):
        self.enviados.append(("aprovacoes", registro_id, corpo))
        return SimpleNamespace(desfecho=SimpleNamespace(value="entregue"))

    def consulta_previa(self, registro_id, corpo):
        self.enviados.append(("consulta", registro_id, corpo))

    def downloads(self, registro_id, corpo):
        self.enviados.append(("downloads", registro_id, corpo))

    def pendentes(self, tipo):
        return set()

    def reenviar_pendentes(self):
        return 0
