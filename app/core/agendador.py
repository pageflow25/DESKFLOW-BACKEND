"""Agendamento dos ciclos do worker (APScheduler, um processo só).

Cada job roda no máximo uma instância por vez (`max_instances=1`) e ciclos
atrasados não se acumulam (`coalesce`). Jobs diferentes podem rodar ao mesmo
tempo: o claim CAS em `fila_processamento` garante que nenhum item é
despachado duas vezes.

São só os quatro ciclos da fila de processamento única. Os ciclos antigos do
PCP (orçamentos, aprovações, downloads e reconciliação), que faziam claim
direto nas tabelas `orcamento_api_*` e devolviam o resultado ao PageFlow por
`POST /api/pcp/retorno/*`, foram removidos: o mesmo trabalho é feito pelos
handlers `pcp.*` da fila, e o resultado é gravado na própria fila.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from .app import Aplicacao

logger = logging.getLogger(__name__)


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
    """Os quatro ciclos da fila de processamento única, num agendador só.

    Eles só entram quando o escalonador foi montado (FILA_ATIVA e catálogo
    presente no banco); sem ele o agendador sobe vazio.

    Sem `LISTEN/NOTIFY`: o banco responde pelo pooler de transação do Supabase,
    onde ele não funciona. O pickup é por poll curto — 1 s no síncrono, que
    cabe folgado nos 30 s de latência aceitável, e 5 s no assíncrono.
    """
    agendador = BlockingScheduler(
        timezone="America/Sao_Paulo",
        job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300},
    )
    fila = getattr(app, "fila", None)
    if fila is None:
        return agendador
    settings = app.settings
    agendador.add_job(
        _protegido("fila_sincrona", fila.ciclo_sincrono),
        "interval", seconds=max(1, settings.FILA_POLL_SINCRONO_SEGUNDOS), id="fila_sincrona",
        misfire_grace_time=5,
    )
    agendador.add_job(
        _protegido("fila_assincrona", fila.ciclo_assincrono),
        "interval", seconds=max(1, settings.FILA_POLL_ASSINCRONO_SEGUNDOS), id="fila_assincrona",
        misfire_grace_time=15,
    )
    agendador.add_job(
        _protegido("fila_reaper", fila.ciclo_reaper),
        "interval", seconds=max(5, settings.FILA_REAPER_SEGUNDOS), id="fila_reaper",
    )
    agendador.add_job(
        _protegido("fila_heartbeat", fila.ciclo_heartbeat),
        "interval", seconds=max(5, settings.FILA_HEARTBEAT_SEGUNDOS), id="fila_heartbeat",
        next_run_time=datetime.now(ZoneInfo("America/Sao_Paulo")),
    )
    return agendador
