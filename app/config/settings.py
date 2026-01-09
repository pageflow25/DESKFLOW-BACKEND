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

    # Configurações DriveHQ FTP 
    DRIVEHQ_FTP_HOST: str
    DRIVEHQ_FTP_PORT: int
    DRIVEHQ_FTP_USER: str
    DRIVEHQ_FTP_PASSWORD: str
    DRIVEHQ_BASE_URL: str = "https://www.drivehq.com"


    # Credenciais Bremen Auth
    DEFAULT_URL: str
    DEFAULT_IDENTIFIER: str
    DEFAULT_USER: str
    DEFAULT_PASSWORD: str
    
    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignora campos extras do .env que não estão no modelo

@lru_cache()
def get_settings():
    return Settings()
