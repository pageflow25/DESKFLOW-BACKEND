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
    
    status_anterior_id = Column(
        Integer,
        ForeignKey('status_deskflow_pedido.id', ondelete='RESTRICT', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="ID do status anterior (pode ser nulo no primeiro evento)"
    )
    
    status_novo_id = Column(
        Integer,
        ForeignKey('status_deskflow_pedido.id', ondelete='RESTRICT', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID do novo status após a transição"
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
    
    grupo_lote_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID do grupo selecionado ao disparar o lote de orçamentos"
    )

    envio_item_id = Column(
        Integer,
        ForeignKey('envio_item.id', ondelete='RESTRICT', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID do item de envio canônico associado ao evento"
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
    
    status_anterior = relationship(
        "StatusDeskflowPedido",
        foreign_keys=[status_anterior_id],
        back_populates="historicos_como_status_anterior"
    )
    
    status_novo = relationship(
        "StatusDeskflowPedido",
        foreign_keys=[status_novo_id],
        back_populates="historicos_como_status_novo"
    )

    envio_item = relationship(
        "EnvioItem",
        back_populates="historicos",
        foreign_keys=[envio_item_id]
    )
    
    def __repr__(self):
        status_ant = self.status_anterior.codigo if self.status_anterior else 'INÍCIO'
        status_nov = self.status_novo.codigo if self.status_novo else 'N/A'
        return f"<HistoricoProcessamento(id={self.id}, distribuicao_id={self.distribuicao_material_id}, {status_ant} -> {status_nov})>"
    
    def __str__(self):
        sucesso_str = "✓" if self.sucesso else "✗"
        status_ant = self.status_anterior.codigo if self.status_anterior else 'INÍCIO'
        status_nov = self.status_novo.codigo if self.status_novo else 'N/A'
        return f"[{sucesso_str}] {status_ant} → {status_nov}: {self.mensagem or 'Sem mensagem'}"
