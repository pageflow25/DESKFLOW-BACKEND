from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .settings import get_settings
from .logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Configuração SSL para PostgreSQL
# Para Render.com, usar 'require' é a melhor opção
# 'require': Força SSL mas não valida certificado (melhor para Render)
# 'prefer': Tenta SSL primeiro, mas aceita sem SSL se falhar
# 'disable': Desabilita SSL completamente
ssl_mode = "require" if settings.DB_SSL else "disable"

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,      # Testa conexões antes de usar - ESSENCIAL
    pool_recycle=60,         # Recicla conexões a cada 60s (antes era 300s)
    pool_size=10,            # Pool de 10 conexões
    max_overflow=20,         # Até 20 conexões adicionais
    pool_timeout=60,         # Timeout de 60s para obter conexão do pool
    echo=False,              # Desabilitar logs automáticos do SQLAlchemy
    # Configurações de conexão para PostgreSQL (Render)
    connect_args={
        "sslmode": ssl_mode,
        "options": "-c timezone=America/Sao_Paulo -c statement_timeout=120000",  # 120s timeout para statements
        # Keepalive para evitar que conexões SSL sejam fechadas inesperadamente
        "keepalives": 1,              # Habilita TCP keepalive
        "keepalives_idle": 15,        # Inicia keepalive após 15s de inatividade (antes 30s)
        "keepalives_interval": 5,     # Intervalo de 5s entre keepalive packets (antes 10s)
        "keepalives_count": 5,        # 5 tentativas antes de considerar conexão morta
        "connect_timeout": 30         # Timeout de 30s para estabelecer conexão (antes 10s)
    }
)

# Criação da session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para os modelos
Base = declarative_base()

# Configurar o schema padrão para todas as tabelas
Base.metadata.schema = "public"

# Dependency para obter sessão do banco
def get_db():
    db = SessionLocal()
    try:
        logger.debug("Sessão do banco de dados criada")
        yield db
        db.commit()  # Commit automático se não houver exceção
        logger.debug("Transação confirmada com sucesso")
    except Exception as e:
        logger.warning(f"Erro detectado, executando rollback: {str(e)}")
        try:
            db.rollback()  # Rollback em caso de erro
            logger.debug("Rollback executado com sucesso")
        except Exception as rollback_error:
            logger.error(f"Erro ao executar rollback: {str(rollback_error)}")
        raise
    finally:
        try:
            db.close()
            logger.debug("Sessão do banco de dados fechada")
        except Exception as close_error:
            logger.error(f"Erro ao fechar sessão: {str(close_error)}")
