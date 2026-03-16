from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class LoteEnvio(Base):
    """Cabeçalho canônico do lote de envio."""

    __tablename__ = "lote_envio"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    identificador_lote = Column(String(100), nullable=False, unique=True, index=True)
    legacy_grupo_lote_id = Column(Integer, nullable=True, unique=True, index=True)
    status = Column(String(40), nullable=False, default="pendente", index=True)
    data_envio = Column(DateTime, nullable=True, index=True)
    data_ultima_atualizacao = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    itens = relationship("EnvioItem", back_populates="lote_envio", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<LoteEnvio(id={self.id}, identificador={self.identificador_lote})>"
