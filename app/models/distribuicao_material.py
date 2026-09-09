from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class DistribuicaoMaterial(Base):
    """
    Modelo DistribuicaoMaterial
    Representa a entrega de um item comercial a um destino (unidade escolar
    e/ou turma) dentro de um formulário — 1 linha por (formulario_id,
    pedido_item_carrinho_id, unidade_escolar_id, id_turma). Os arquivos e
    especificações que compõem essa entrega (capa, miolo etc.) ficam em
    PedidoDistribuicaoArquivo (relacionamento `materiais`).
    """
    __tablename__ = "pedido_distribuicoes"

    id = Column(Integer, primary_key=True, index=True)
    grupo_id = Column(Integer, nullable=True, comment="ID do grupo/lote mais recente atribuído à distribuição")
    grupo_lote_ids = Column(Text, nullable=True, comment="JSON array com histórico de todos os grupo_lote_ids atribuídos — nunca sobrescreve, apenas acumula")
    quantidade = Column(Integer, nullable=False, default=0, comment="Quantidade de material destinada à unidade")
    observacoes = Column(Text, nullable=True, comment="Observações específicas para esta distribuição")
    data_saida = Column(DateTime, nullable=True, comment="Data real da saída do material da unidade")
    formulario_id = Column(
        Integer,
        ForeignKey('pedido_formularios.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        comment="ID do formulário ao qual esta distribuição pertence"
    )
    unidade_escolar_id = Column(
        Integer,
        ForeignKey('escola_unidades.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da unidade escolar que receberá o material - pode ser nulo para distribuições virtuais por turma"
    )
    id_turma = Column(
        Integer,
        ForeignKey('escola_turmas.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da turma que receberá o material (se aplicável)"
    )
    pedido_item_carrinho_id = Column(
        Integer,
        nullable=True,
        comment="Item do carrinho (pedido_itens_carrinho) ao qual esta distribuição pertence"
    )
    chave_entrega = Column(
        String(255),
        nullable=True,
        unique=True,
        comment="Chave determinística 'f:<formulario>|i:<item>|u:<unidade>|t:<turma>' gerada por trigger no banco"
    )
    # Campos para integração DeskFlow
    status_id = Column(
        Integer,
        ForeignKey('status_deskflow_pedido.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID do status atual do processamento DeskFlow"
    )
    id_orcamento = Column(Integer, nullable=True, comment="ID do orçamento retornado pela API DeskFlow")
    id_ops = Column(Integer, nullable=True, comment="ID das OPs (Ordens de Produção) geradas")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relacionamentos
    formulario = relationship("Formulario", back_populates="distribuicoes")
    unidade_escolar = relationship("UnidadeEscolar", back_populates="distribuicoes")
    turma = relationship("Turma", back_populates="distribuicoes")
    materiais = relationship(
        "PedidoDistribuicaoArquivo",
        back_populates="distribuicao",
        cascade="all, delete-orphan"
    )
    status_deskflow = relationship("StatusDeskflowPedido", back_populates="distribuicoes")
    orcamento_api = relationship("OrcamentoAPI", back_populates="distribuicao_material")
    orcamento_faturamento = relationship("OrcamentoFaturamento", back_populates="distribuicao_material", uselist=False)
    aprovacao_api = relationship("AprovacaoAPI", back_populates="distribuicao_material")
    historico_processamento = relationship("HistoricoProcessamento", back_populates="distribuicao_material")
    downloads_bremen = relationship("DownloadBremen", back_populates="distribuicao_material")
    envio_itens = relationship("EnvioItem", back_populates="distribuicao_material")

    # Índices
    __table_args__ = (
        Index('idx_distribuicao_formulario', 'formulario_id'),
        Index('idx_distribuicao_unidade', 'unidade_escolar_id'),
        Index('idx_distribuicao_turma', 'id_turma'),
    )

    def __repr__(self):
        return f"<DistribuicaoMaterial(id={self.id}, formulario_id={self.formulario_id}, status_id={self.status_id})>"
