"""
Configuração de logging para o sistema DESKFLOW PCP
"""
import logging
import logging.handlers
import sys
from pathlib import Path
from datetime import datetime
from .settings import get_settings

settings = get_settings()

# Criar diretório de logs se não existir
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Formato dos logs
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Nome do arquivo de log com data
LOG_FILE = LOGS_DIR / f"deskflow_{datetime.now().strftime('%Y%m%d')}.log"
ERROR_LOG_FILE = LOGS_DIR / f"deskflow_errors_{datetime.now().strftime('%Y%m%d')}.log"


def setup_logging(level: str = None) -> None:
    """
    Configura o sistema de logging da aplicação
    
    Args:
        level: Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Define o nível baseado nas configurações ou parâmetro
    log_level = level or ("DEBUG" if settings.DEBUG else "INFO")
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Configuração do logger raiz
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remover handlers existentes para evitar duplicação
    root_logger.handlers.clear()
    
    # Formatter
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    
    # 1. Handler para console (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 2. Handler para arquivo geral (todos os logs)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # 3. Handler para arquivo de erros (apenas ERROR e CRITICAL)
    error_handler = logging.handlers.RotatingFileHandler(
        ERROR_LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)
    
    # Configurar loggers de bibliotecas externas
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.orm").setLevel(logging.WARNING)
    
    # Silenciar warnings do passlib sobre bcrypt
    logging.getLogger("passlib").setLevel(logging.ERROR)
    
    # Log inicial
    root_logger.info("=" * 80)
    root_logger.info(f"Sistema DESKFLOW PCP iniciado - Nível de log: {log_level}")
    root_logger.info(f"Debug mode: {settings.DEBUG}")
    root_logger.info(f"Logs salvos em: {LOGS_DIR}")
    root_logger.info("=" * 80)


def get_logger(name: str) -> logging.Logger:
    """
    Retorna um logger configurado para um módulo específico
    
    Args:
        name: Nome do módulo (use __name__)
        
    Returns:
        Logger configurado
        
    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Mensagem de log")
    """
    return logging.getLogger(name)
