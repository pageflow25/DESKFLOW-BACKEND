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
    
    # Configurações da API Bremen (Orçamento)
    BREMEN_API_URL: str = "http://192.168.1.215:9001"
    BREMEN_API_TOKEN: str = "Bearer ZXlKMGVYQWlPaUpLVjFRaUxDSmhiR2NpT2lKSVV6STFOaUo5LmV5SjFjM1ZoY21sdklqb2ljR0ZuWldac2IzY2lMQ0p1ZFcxbGNtOWZjMlZ5YVdVaU9pSXdNemc1T1NJc0ltVjRjQ0k2TVRjMk9ETTBPRGt6TUgwLkhYUGl1WEoyRUJUa1JDQXZydG9PV2ZVZ1llMHJTNUp6UU9maGo0eG1LVUk="
    
    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignora campos extras do .env que não estão no modelo

@lru_cache()
def get_settings():
    return Settings()
