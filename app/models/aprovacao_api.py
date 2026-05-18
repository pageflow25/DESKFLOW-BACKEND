from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class AprovacaoAPI(Base):
    """
    Modelo AprovacaoAPI
    Armazena o retorno da API de aprovação de orçamento (FASE 02 do DeskFlow)
    
    Cada OP gera uma linha separada na tabela.
    O id_orcamento se repete para cada OP do mesmo orçamento.
    O distribuicao_material_id corresponde sequencialmente ao id_distribuicao do request.
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
        ForeignKey('pedido_distribuicoes.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID da distribuição de material vinculada a esta aprovação (um por OP)"
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
        comment="Um único ID de OP (Ordem de Produção) — uma linha por OP"
    )
    
    id_pedido_venda = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID do pedido de venda gerado na aprovação"
    )
    
    pedidos = Column(
        JSONB,
        nullable=True,
        comment="Um único objeto de pedido {id, serie, empresa} em formato JSON"
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

    envio_item_id = Column(
        Integer,
        ForeignKey('envio_item.id', ondelete='RESTRICT', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID do item de envio canônico associado a esta aprovação"
    )
    
    # Relacionamentos
    distribuicao_material = relationship(
        "DistribuicaoMaterial",
        back_populates="aprovacao_api",
        foreign_keys=[distribuicao_material_id]
    )

    envio_item = relationship(
        "EnvioItem",
        back_populates="aprovacoes",
        foreign_keys=[envio_item_id]
    )
    
    def __repr__(self):
        return f"<AprovacaoAPI(id={self.id}, distribuicao_material_id={self.distribuicao_material_id}, id_orcamento={self.id_orcamento})>"
    
    def __str__(self):
        return f"Aprovação API #{self.id} - Orçamento: {self.id_orcamento or 'N/A'}, OPs: {self.id_ops or 'N/A'}"
