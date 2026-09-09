from sqlalchemy import Column, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.config.database import Base

class PedidoDistribuicaoArquivo(Base):
    """
    Materiais (arquivos) que compõem uma entrega comercial.

    Uma DistribuicaoMaterial é 1 linha por item comercial entregue a um
    destino (formulário + item de carrinho + unidade/turma); aqui ficam os
    1+ arquivos/especificações/componentes que essa entrega carrega (ex.:
    capa e miolo de um mesmo livreto). Quantidade e status pertencem à
    DistribuicaoMaterial, não a esta tabela.
    """
    __tablename__ = "pedido_distribuicao_arquivos"

    distribuicao_material_id = Column(
        Integer,
        ForeignKey('pedido_distribuicoes.id', ondelete='CASCADE', onupdate='CASCADE'),
        primary_key=True,
        nullable=False,
        comment="Entrega comercial à qual este material pertence"
    )
    arquivo_pdf_id = Column(
        Integer,
        ForeignKey('pedido_arquivos_pdf.id', ondelete='CASCADE', onupdate='CASCADE'),
        primary_key=True,
        nullable=False,
        comment="Arquivo PDF entregue"
    )
    especificacao_form_id = Column(
        Integer,
        ForeignKey('pedido_especificacoes.id', ondelete='RESTRICT', onupdate='CASCADE'),
        nullable=False,
        comment="Especificação (papel, gramatura, cores etc.) deste arquivo"
    )
    id_componente = Column(
        Integer,
        ForeignKey('bremen_componentes.id_componente', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="Componente Bremen (capa/miolo/etc.) representado pelo arquivo"
    )

    # Relacionamentos
    distribuicao = relationship("DistribuicaoMaterial", back_populates="materiais")
    arquivo_pdf = relationship("ArquivoPdf", back_populates="entregas")
    especificacao = relationship("EspecificacaoForm", back_populates="distribuicao_arquivos")
    componente = relationship("BremenComponente", foreign_keys=[id_componente])

    __table_args__ = (
        Index('idx_pedido_distribuicao_arquivos_arquivo', 'arquivo_pdf_id'),
        Index('idx_pedido_distribuicao_arquivos_especificacao', 'especificacao_form_id'),
        Index('idx_pedido_distribuicao_arquivos_componente', 'id_componente'),
    )

    def __repr__(self):
        return (
            f"<PedidoDistribuicaoArquivo(distribuicao_material_id={self.distribuicao_material_id}, "
            f"arquivo_pdf_id={self.arquivo_pdf_id})>"
        )
