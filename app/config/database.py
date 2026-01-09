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
    pool_pre_ping=True,  # Testa conexões antes de usar
    pool_recycle=300,    # Recicla conexões a cada 5 minutos
    pool_size=10,        # Pool de 10 conexões
    max_overflow=20,     # Até 20 conexões adicionais
    echo=False,          # Desabilitar logs automáticos do SQLAlchemy
    # Configurações de conexão para PostgreSQL (Render)
    connect_args={
        "sslmode": ssl_mode,
        "options": "-c timezone=America/Sao_Paulo",
        # Keepalive para evitar que conexões SSL sejam fechadas inesperadamente
        "keepalives": 1,              # Habilita TCP keepalive
        "keepalives_idle": 30,        # Inicia keepalive após 30s de inatividade
        "keepalives_interval": 10,    # Intervalo de 10s entre keepalive packets
        "keepalives_count": 5,        # 5 tentativas antes de considerar conexão morta
        "connect_timeout": 10         # Timeout de 10s para estabelecer conexão
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
        db.rollback()  # Rollback em caso de erro
        logger.error(f"Erro na transação - Rollback executado: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()
        logger.debug("Sessão do banco de dados fechada")
