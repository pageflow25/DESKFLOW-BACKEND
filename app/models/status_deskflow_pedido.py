from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.config.database import Base


class StatusDeskflowPedido(Base):
    """
    Modelo StatusDeskflowPedido
    Centraliza todos os status possíveis para o processamento de pedidos via DeskFlow
    """
    __tablename__ = "status_deskflow_pedido"
    
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True
    )
    
    codigo = Column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        comment="Código único do status (ex: pedido_recebido, arquivos_processados)"
    )
    
    descricao = Column(
        String(100),
        nullable=False,
        comment="Descrição legível do status"
    )
    
    # Relacionamentos
    distribuicoes = relationship(
        "DistribuicaoMaterial",
        back_populates="status_deskflow",
        foreign_keys="[DistribuicaoMaterial.status_id]"
    )
    
    # Relacionamentos com HistoricoProcessamento
    historicos_como_status_anterior = relationship(
        "HistoricoProcessamento",
        foreign_keys="[HistoricoProcessamento.status_anterior_id]",
        back_populates="status_anterior"
    )
    
    historicos_como_status_novo = relationship(
        "HistoricoProcessamento",
        foreign_keys="[HistoricoProcessamento.status_novo_id]",
        back_populates="status_novo"
    )
    
    def __repr__(self):
        return f"<StatusDeskflowPedido(id={self.id}, codigo='{self.codigo}', descricao='{self.descricao}')>"
    
    def __str__(self):
        return f"{self.codigo} - {self.descricao}"
