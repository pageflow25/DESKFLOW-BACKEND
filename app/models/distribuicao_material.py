from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Date, DECIMAL, Enum as SQLEnum, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base
import enum

class StatusDistribuicao(str, enum.Enum):
    """Enum para status de distribuição"""
    PENDENTE = "pendente"
    EM_PRODUCAO = "em_producao"
    PRODUZIDO = "produzido"
    ENVIADO = "enviado"
    ENTREGUE = "entregue"
    CANCELADO = "cancelado"

class DistribuicaoMaterial(Base):
    """
    Modelo DistribuicaoMaterial
    Representa a distribuição de materiais para unidades escolares em um formulário
    """
    __tablename__ = "pedido_distribuicoes"
    
    id = Column(Integer, primary_key=True, index=True)
    grupo_id = Column(Integer, nullable=True, comment="ID do grupo/lote mais recente atribuído à distribuição")
    grupo_lote_ids = Column(Text, nullable=True, comment="JSON array com histórico de todos os grupo_lote_ids atribuídos — nunca sobrescreve, apenas acumula")
    quantidade = Column(Integer, nullable=False, default=0, comment="Quantidade de material destinada à unidade")
    descricao_material = Column(Text, nullable=True, comment="Descrição específica do material para esta unidade")
    observacoes = Column(Text, nullable=True, comment="Observações específicas para esta distribuição")
    status_distribuicao = Column(
        String(50),
        nullable=False,
        default="pendente",
        comment="Status da distribuição para esta unidade"
    )
    data_saida = Column(String(255), nullable=True, comment="Data real da saída do material da unidade")
    arquivo_pdf_id = Column(
        Integer,
        ForeignKey('pedido_arquivos_pdf.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID do arquivo PDF relacionado a esta distribuição"
    )
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
    especificacao_form_id = Column(
        Integer,
        ForeignKey('pedido_especificacoes.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da especificação do formulário (se aplicável)"
    )
    id_turma = Column(
        Integer,
        ForeignKey('escola_turmas.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da turma que receberá o material (se aplicável)"
    )
    # Campos para integração DeskFlow
    status_id = Column(
        Integer,
        ForeignKey('status_deskflow_pedido.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID do status atual do processamento DeskFlow"
    )
    path_arquivos = Column(Text, nullable=True, comment="Caminho dos arquivos processados")
    id_orcamento = Column(Integer, nullable=True, comment="ID do orçamento retornado pela API DeskFlow")
    id_ops = Column(Integer, nullable=True, comment="ID das OPs (Ordens de Produção) geradas")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    formulario = relationship("Formulario", back_populates="distribuicoes")
    unidade_escolar = relationship("UnidadeEscolar", back_populates="distribuicoes")
    turma = relationship("Turma", back_populates="distribuicoes")
    especificacao = relationship("EspecificacaoForm", back_populates="distribuicoes")
    arquivo_pdf = relationship("ArquivoPdf")
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
        Index('idx_distribuicao_especificacao', 'especificacao_form_id'),
        Index('idx_distribuicao_status', 'status_distribuicao'),
    )
    
    def __repr__(self):
        return f"<DistribuicaoMaterial(id={self.id}, formulario_id={self.formulario_id}, status={self.status_distribuicao})>"
