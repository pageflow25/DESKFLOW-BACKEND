"""Conexão com o Postgres compartilhado com o PageFlow.

Regra da casa: transações curtas. Nenhuma transação fica aberta durante uma
chamada HTTP (ERP, PageFlow ou download) — o claim é gravado e commitado antes
de chamar o ERP.
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .config import get_settings


# Conexões que os ciclos antigos (orçamentos, aprovações, downloads e
# reconciliação) podem querer ao mesmo tempo: cada um roda uma instância por
# vez, mas os quatro rodam em paralelo.
CONEXOES_JOBS_ANTIGOS = 4
# Os quatro ciclos novos da fila que abrem transação própria: despachante
# síncrono, despachante assíncrono, reaper e heartbeat.
CONEXOES_CICLOS_FILA = 4


def dimensionar_pool(settings) -> int:
    """Pool explícito, não por acidente.

    Cada slot dos pools da fila pede duas conexões: a thread que executa o item
    (preparo e aplicação do desfecho) e o renovador de lease, que roda em
    paralelo enquanto a chamada está em voo.
    """
    if settings.DB_POOL_SIZE > 0:
        return settings.DB_POOL_SIZE
    if not settings.FILA_ATIVA:
        return CONEXOES_JOBS_ANTIGOS + 1
    slots = max(1, settings.FILA_POOL_SINCRONO) + max(1, settings.FILA_POOL_ASSINCRONO)
    return CONEXOES_JOBS_ANTIGOS + CONEXOES_CICLOS_FILA + slots * 2


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    opcoes = f"-c timezone=America/Sao_Paulo -c statement_timeout={settings.DB_STATEMENT_TIMEOUT_MS}"
    return create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=dimensionar_pool(settings),
        max_overflow=settings.DB_MAX_OVERFLOW,
        connect_args={
            "sslmode": "require" if settings.DB_SSL else "disable",
            "options": opcoes,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
