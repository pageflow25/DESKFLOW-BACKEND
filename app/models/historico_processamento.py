from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class HistoricoProcessamento(Base):
    """
    Modelo HistoricoProcessamento
    Log de eventos e transições entre fases do processamento DeskFlow
    """
    __tablename__ = "historico_processamento"
    
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
        index=True,
        comment="ID da distribuição de material vinculada a este histórico"
    )
    
    status_anterior = Column(
        String(50),
        nullable=True,
        comment="Código do status anterior (pode ser nulo no primeiro evento)"
    )
    
    status_novo = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Código do novo status após a transição"
    )
    
    mensagem = Column(
        Text,
        nullable=True,
        comment="Mensagem descritiva do evento ou erro ocorrido"
    )
    
    sucesso = Column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        comment="Indica se a operação foi bem-sucedida"
    )
    
    data_evento = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="Data e hora em que o evento ocorreu"
    )
    
    # Relacionamentos
    distribuicao_material = relationship(
        "DistribuicaoMaterial",
        back_populates="historico_processamento",
        foreign_keys=[distribuicao_material_id]
    )
    
    def __repr__(self):
        status_str = f"{self.status_anterior or 'INÍCIO'} -> {self.status_novo}"
        return f"<HistoricoProcessamento(id={self.id}, distribuicao_id={self.distribuicao_material_id}, {status_str})>"
    
    def __str__(self):
        sucesso_str = "✓" if self.sucesso else "✗"
        return f"[{sucesso_str}] {self.status_anterior or 'INÍCIO'} → {self.status_novo}: {self.mensagem or 'Sem mensagem'}"
