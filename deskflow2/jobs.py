"""Agendamento dos ciclos do worker (APScheduler, um processo só).

Cada job roda no máximo uma instância por vez (`max_instances=1`) e ciclos
atrasados não se acumulam (`coalesce`). Jobs diferentes podem rodar ao mesmo
tempo: o claim CAS no banco garante que nenhuma linha é despachada duas vezes.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from .app import Aplicacao

logger = logging.getLogger(__name__)

# A reconciliação roda no ritmo de PCP_RECONCILIACAO_MINUTOS, entre 1 e 5 min,
# e a primeira vez logo depois de subir (não espera o primeiro intervalo).
INTERVALO_RECONCILIACAO_MIN_SEGUNDOS = 60
INTERVALO_RECONCILIACAO_MAX_SEGUNDOS = 300
ATRASO_PRIMEIRA_RECONCILIACAO_SEGUNDOS = 15


def intervalo_reconciliacao(minutos: int) -> int:
    return min(INTERVALO_RECONCILIACAO_MAX_SEGUNDOS, max(INTERVALO_RECONCILIACAO_MIN_SEGUNDOS, minutos * 60))


def _protegido(nome: str, ciclo):
    def executar():
        try:
            quantidade = ciclo()
            if quantidade:
                logger.info("Ciclo %s: %s registro(s) processado(s)", nome, quantidade)
        except Exception:
            # Um ciclo com erro não derruba o worker; o próximo tenta de novo.
            logger.exception("Ciclo %s falhou", nome)
    return executar


def criar_agendador(app: Aplicacao) -> BlockingScheduler:
    intervalo = max(10, app.settings.PCP_ENVIO_INTERVALO_SEGUNDOS)
    agendador = BlockingScheduler(
        timezone="America/Sao_Paulo",
        job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300},
    )
    agendador.add_job(_protegido("orcamentos", app.orcamentos.executar_ciclo), "interval", seconds=intervalo, id="orcamentos")
    agendador.add_job(_protegido("aprovacoes", app.aprovacoes.executar_ciclo), "interval", seconds=intervalo, id="aprovacoes")
    agendador.add_job(_protegido("downloads", app.downloads.executar_ciclo), "interval", seconds=intervalo, id="downloads")
    agendador.add_job(
        _protegido("reconciliacao", app.reconciliacao.executar_ciclo),
        "interval", seconds=intervalo_reconciliacao(app.settings.PCP_RECONCILIACAO_MINUTOS), id="reconciliacao",
        next_run_time=datetime.now(ZoneInfo("America/Sao_Paulo")) + timedelta(seconds=ATRASO_PRIMEIRA_RECONCILIACAO_SEGUNDOS),
    )
    return agendador
