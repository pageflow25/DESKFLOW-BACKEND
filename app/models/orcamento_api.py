from sqlalchemy import Column, Integer, DateTime, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class OrcamentoAPI(Base):
    """
    Modelo OrcamentoAPI
    Armazena o retorno da API de criação de orçamento (FASE 02 do DeskFlow)
    
    Cada item do orçamento gera uma linha separada na tabela.
    O id_orcamento se repete para cada item do mesmo orçamento.
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
        ForeignKey('pedido_distribuicoes.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID da distribuição de material vinculada a este orçamento"
    )
    
    id_orcamento = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID do orçamento retornado pela API do DeskFlow"
    )
    
    id_item = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID do item específico dentro do orçamento"
    )
    
    itens = Column(
        JSONB,
        nullable=True,
        comment="Dados do item do orçamento em formato JSON (um objeto por registro)"
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

    envio_item_id = Column(
        Integer,
        ForeignKey('envio_item.id', ondelete='RESTRICT', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID do item de envio canônico associado a este orçamento"
    )
    
    # Relacionamentos
    distribuicao_material = relationship(
        "DistribuicaoMaterial",
        back_populates="orcamento_api",
        foreign_keys=[distribuicao_material_id]
    )

    envio_item = relationship(
        "EnvioItem",
        back_populates="orcamentos",
        foreign_keys=[envio_item_id]
    )
    
    # Índice único para evitar duplicatas do mesmo item
    __table_args__ = (
        Index(
            'idx_orcamento_api_unique_item',
            'distribuicao_material_id',
            'id_orcamento',
            'id_item',
            unique=True
        ),
    )
    
    def __repr__(self):
        return f"<OrcamentoAPI(id={self.id}, distribuicao_material_id={self.distribuicao_material_id}, id_orcamento={self.id_orcamento}, id_item={self.id_item})>"
    
    def __str__(self):
        return f"Orçamento API #{self.id} - Orçamento: {self.id_orcamento or 'N/A'}, Item: {self.id_item or 'N/A'}"
