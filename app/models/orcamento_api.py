from sqlalchemy import Column, Integer, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class OrcamentoAPI(Base):
    """
    Modelo OrcamentoAPI
    Armazena o retorno da API de criação de orçamento (FASE 02 do DeskFlow)
    """
    __tablename__ = "orcamento_api"
    
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True
    )
    
    distribuicao_material_id = Column(
        Integer,
        ForeignKey('distribuicao_materiais.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        unique=True,
        index=True,
        comment="ID da distribuição de material vinculada a este orçamento"
    )
    
    id_orcamento = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID do orçamento retornado pela API do DeskFlow"
    )
    
    itens = Column(
        JSONB,
        nullable=True,
        comment="Array de itens do orçamento em formato JSON"
    )
    
    resposta_api = Column(
        JSONB,
        nullable=True,
        comment="Resposta completa da API de criação de orçamento"
    )
    
    criado_em = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="Data e hora de criação do registro"
    )
    
    # Relacionamentos
    distribuicao_material = relationship(
        "DistribuicaoMaterial",
        back_populates="orcamento_api",
        foreign_keys=[distribuicao_material_id]
    )
    
    def __repr__(self):
        return f"<OrcamentoAPI(id={self.id}, distribuicao_material_id={self.distribuicao_material_id}, id_orcamento={self.id_orcamento})>"
    
    def __str__(self):
        return f"Orçamento API #{self.id} - Orçamento DeskFlow: {self.id_orcamento or 'N/A'}"
