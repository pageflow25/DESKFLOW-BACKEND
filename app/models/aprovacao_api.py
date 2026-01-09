from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class AprovacaoAPI(Base):
    """
    Modelo AprovacaoAPI
    Armazena o retorno da API de aprovação de orçamento (FASE 03 do DeskFlow)
    """
    __tablename__ = "aprovacao_api"
    
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
        comment="ID da distribuição de material vinculada a esta aprovação"
    )
    
    id_orcamento = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID do orçamento aprovado"
    )
    
    id_ops = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID das OPs (Ordens de Produção) geradas"
    )
    
    pedidos = Column(
        JSONB,
        nullable=True,
        comment="Array de pedidos gerados na aprovação em formato JSON"
    )
    
    resposta_api = Column(
        JSONB,
        nullable=True,
        comment="Resposta completa da API de aprovação de orçamento"
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
        back_populates="aprovacao_api",
        foreign_keys=[distribuicao_material_id]
    )
    
    def __repr__(self):
        return f"<AprovacaoAPI(id={self.id}, distribuicao_material_id={self.distribuicao_material_id}, id_orcamento={self.id_orcamento})>"
    
    def __str__(self):
        return f"Aprovação API #{self.id} - Orçamento: {self.id_orcamento or 'N/A'}, OPs: {self.id_ops or 'N/A'}"
