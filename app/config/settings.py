from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # Configurações do banco de dados 
    DATABASE_URL: str
    DB_SSL: bool


    # Configurações JWT
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    
    # Configurações da aplicação 
    APP_NAME: str
    DEBUG: bool
    NODE_ENV: str

    # Vercel Blob Storage (armazenamento de PDFs)
    BLOB_READ_WRITE_TOKEN: str

    # Credenciais Bremen Auth
    DEFAULT_URL: str
    DEFAULT_IDENTIFIER: str
    DEFAULT_USER: str
    DEFAULT_PASSWORD: str
    
    # Configurações da API Bremen (Orçamento e Aprovação)
    BREMEN_API_URL: str
    BREMEN_API_TOKEN: str = ""  # Opcional — token agora é renovado automaticamente via BremenClient
    API_TIMEOUT: int  # 5 minutos para aguardar resposta da API Bremen
    BREMEN_503_MAX_WAIT_SECONDS: int = 300
    BREMEN_503_RETRY_BASE_SECONDS: int = 5
    BREMEN_503_RETRY_MAX_INTERVAL_SECONDS: int = 30

    # Configurações FASE 03 — Download de Arquivos
    DOWNLOAD_BASE_PATH: str

    # Configurações da automação de escolas conveniadas
    CONVENIADO_AUTOMACAO_ATIVA: bool
    CONVENIADO_AUTOMACAO_HORARIOS: str
    CONVENIADO_AUTOMACAO_TIMEZONE: str
    CONVENIADO_DATA_ENTREGA_OFFSET_DIAS: int
    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignora campos extras do .env que não estão no modelo

@lru_cache()
def get_settings():
    return Settings()
