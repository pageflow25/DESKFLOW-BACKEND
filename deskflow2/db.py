"""Conexão com o Postgres compartilhado com o PageFlow.

Regra da casa: transações curtas. Nenhuma transação fica aberta durante uma
chamada HTTP (ERP, PageFlow ou download) — o claim é gravado e commitado antes
de chamar o ERP.
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    opcoes = f"-c timezone=America/Sao_Paulo -c statement_timeout={settings.DB_STATEMENT_TIMEOUT_MS}"
    return create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "sslmode": "require" if settings.DB_SSL else "disable",
            "options": opcoes,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )
