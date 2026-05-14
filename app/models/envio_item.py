from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class EnvioItem(Base):
    """Item de envio de uma distribuição em um lote específico."""

    __tablename__ = "envio_item"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    lote_envio_id = Column(
        Integer,
        ForeignKey("lote_envio.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    distribuicao_material_id = Column(
        Integer,
        ForeignKey("pedido_distribuicoes.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
    )
    status_envio = Column(String(50), nullable=False, default="pendente", index=True)
    sucesso_ultimo_evento = Column(Boolean, nullable=False, default=False)
    id_orcamento_snapshot = Column(Integer, nullable=True)
    id_ops_snapshot = Column(Integer, nullable=True)
    arquivo_nome_snapshot = Column(String(255), nullable=True)
    escola_id_snapshot = Column(Integer, nullable=True)
    formulario_id_snapshot = Column(Integer, nullable=True)
    criado_em = Column(DateTime, nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    lote_envio = relationship("LoteEnvio", back_populates="itens")
    distribuicao_material = relationship("DistribuicaoMaterial", back_populates="envio_itens")
    historicos = relationship("HistoricoProcessamento", back_populates="envio_item")
    aprovacoes = relationship("AprovacaoAPI", back_populates="envio_item")
    orcamentos = relationship("OrcamentoAPI", back_populates="envio_item")

    __table_args__ = (
        Index("uq_envio_item_lote_distribuicao", "lote_envio_id", "distribuicao_material_id", unique=True),
    )

    def __repr__(self):
        return (
            f"<EnvioItem(id={self.id}, lote_envio_id={self.lote_envio_id}, "
            f"dist={self.distribuicao_material_id})>"
        )
