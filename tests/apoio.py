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
        BLOB_READ_WRITE_TOKEN="blob-token",
        DOWNLOAD_BASE_PATH="",
        DOWNLOAD_TIMEOUT=5,
        DOWNLOAD_TENTATIVAS=2,
        # Fase 6b: com os ciclos antigos removidos, os unicos ciclos do worker
        # sao os da fila, e `criar_agendador` le estes quatro intervalos.
        FILA_POLL_SINCRONO_SEGUNDOS=1,
        FILA_POLL_ASSINCRONO_SEGUNDOS=5,
        FILA_REAPER_SEGUNDOS=30,
        FILA_HEARTBEAT_SEGUNDOS=30,
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

