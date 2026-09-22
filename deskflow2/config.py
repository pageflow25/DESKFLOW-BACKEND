"""Configuração do DESKFLOW2.0, lida do `.env` (ver `.env.example`)."""

import os
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # DESKFLOW2_ENV_FILE escolhe outro arquivo (ex.: .env.testing) sem mexer no .env.
    model_config = SettingsConfigDict(
        env_file=os.environ.get("DESKFLOW2_ENV_FILE", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Banco compartilhado com o PageFlow (as tabelas orcamento_api_* são dele).
    DATABASE_URL: str
    DB_SSL: bool = True
    DB_STATEMENT_TIMEOUT_MS: int = 120_000

    # ERP Wingraph (a API é servida pela Bremen Sistemas).
    ERP_BASE_URL: str
    ERP_USER: str
    ERP_PASSWORD: str
    ERP_IDENTIFIER: str = "PageFlow"
    ERP_TIMEOUT: float = 120.0
    # A doc diz que o token vale 2h e não tem refresh: renova antes de vencer.
    ERP_TOKEN_VIDA_SEGUNDOS: int = 7200
    ERP_TOKEN_MARGEM_SEGUNDOS: int = 300
    ERP_503_MAX_WAIT_SECONDS: int = 300
    ERP_503_RETRY_BASE_SECONDS: int = 5
    ERP_503_RETRY_MAX_INTERVAL_SECONDS: int = 30

    # PageFlow: só recebe o resultado (POST /api/pcp/retorno/...).
    PAGEFLOW_API_URL: str
    PAGEFLOW_API_KEY: str
    PAGEFLOW_TIMEOUT: float = 30.0
    PAGEFLOW_TENTATIVAS: int = 5

    # Fila do PCP.
    # Padrão para linha sem `modo_envio`; o modo gravado na linha tem prioridade.
    PCP_MODO_ENVIO: Literal["assincrono", "sincrono"] = "assincrono"
    PCP_ENVIO_ATIVO: bool = True
    PCP_ENVIO_INTERVALO_SEGUNDOS: int = 60
    PCP_ENVIO_LOTE_MAXIMO: int = 20
    PCP_PAUSA_ENTRE_ENVIOS_SEGUNDOS: float = 3.0
    PCP_RECONCILIACAO_MINUTOS: int = 30
    # O ERP tenta o webhook 5x, de hora em hora: depois disso, desiste da linha.
    PCP_RECONCILIACAO_LIMITE_HORAS: int = 6
    PCP_DOWNLOAD_REINICIO_MINUTOS: int = 60

    # Download dos arquivos da OP.
    BLOB_READ_WRITE_TOKEN: str = ""
    DOWNLOAD_BASE_PATH: str = ""
    DOWNLOAD_TIMEOUT: float = 120.0
    DOWNLOAD_TENTATIVAS: int = 3

    # Repasses ao PageFlow que não puderam ser entregues ficam guardados aqui
    # e são reenviados pela reconciliação (o resultado do ERP não se perde).
    DADOS_DIR: str = "dados"

    LOG_DIR: str = "logs"
    LOG_LEVEL: str = "INFO"

    @field_validator("ERP_BASE_URL")
    @classmethod
    def _sem_barra_final(cls, valor: str) -> str:
        return valor.rstrip("/")

    @field_validator("PAGEFLOW_API_URL")
    @classmethod
    def _base_sem_api(cls, valor: str) -> str:
        # As rotas já começam com /api: uma base terminando em /api dobrava o
        # caminho (/api/api/pcp/...) no consumidor antigo.
        valor = valor.rstrip("/")
        if valor.endswith("/api"):
            valor = valor[: -len("/api")]
        return valor


@lru_cache
def get_settings() -> Settings:
    return Settings()
