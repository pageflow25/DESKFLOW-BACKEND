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
    __tablename__ = "distribuicao_materiais"
    
    id = Column(Integer, primary_key=True, index=True)
    grupo_id = Column(Integer, nullable=True, comment="ID do grupo relacionado à distribuição - pode ser nulo para distribuições por turma")
    quantidade = Column(Integer, nullable=False, default=0, comment="Quantidade de material destinada à unidade")
    descricao_material = Column(Text, nullable=True, comment="Descrição específica do material para esta unidade")
    observacoes = Column(Text, nullable=True, comment="Observações específicas para esta distribuição")
    status_distribuicao = Column(
        SQLEnum(StatusDistribuicao),
        nullable=False,
        default=StatusDistribuicao.PENDENTE,
        comment="Status da distribuição para esta unidade"
    )
    data_previsao_entrega = Column(DateTime, nullable=True, comment="Data prevista para entrega na unidade")
    data_saida = Column(String(255), nullable=True, comment="Data real da saída do material da unidade")
    endereco_entrega = Column(Text, nullable=True, comment="Endereço específico para entrega (se diferente do cadastro da unidade)")
    responsavel_recebimento = Column(String(255), nullable=True, comment="Nome do responsável pelo recebimento na unidade")
    telefone_contato = Column(String(20), nullable=True, comment="Telefone de contato para entrega")
    codigo_rastreamento = Column(String(100), nullable=True, comment="Código de rastreamento do envio")
    valor_unitario = Column(DECIMAL(10, 2), nullable=True, comment="Valor unitário do material")
    valor_total = Column(DECIMAL(10, 2), nullable=True, comment="Valor total para esta distribuição (quantidade × valor unitário)")
    ordem_producao = Column(Integer, nullable=True, comment="Ordem de produção/prioridade (1 = mais prioritário)")
    lote_producao = Column(String(50), nullable=True, comment="Identificador do lote de produção")
    arquivo_pdf_id = Column(
        Integer,
        ForeignKey('arquivo_pdfs.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID do arquivo PDF relacionado a esta distribuição"
    )
    formulario_id = Column(
        Integer,
        ForeignKey('formularios.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        comment="ID do formulário ao qual esta distribuição pertence"
    )
    unidade_escolar_id = Column(
        Integer,
        ForeignKey('unidades_escolares.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da unidade escolar que receberá o material - pode ser nulo para distribuições virtuais por turma"
    )
    especificacao_form_id = Column(
        Integer,
        ForeignKey('especificacoes_form.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da especificação do formulário (se aplicável)"
    )
    id_turma = Column(
        Integer,
        ForeignKey('turmas.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID da turma que receberá o material (se aplicável)"
    )
    area = Column(
        String(100),
        nullable=True,
        comment="Área de ensino para distribuição por turma (ex: 'Educação Infantil', 'Ensino Fundamental', etc.)"
    )
    status_id = Column(
        Integer,
        ForeignKey('status_deskflow_pedido.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="ID do status atual do processamento DeskFlow"
    )
    path_arquivos = Column(Text, nullable=True, comment="Caminho dos arquivos processados")
    id_orcamento = Column(Integer, nullable=True, comment="ID do orçamento retornado pela API DeskFlow")
    id_ops = Column(Integer, nullable=True, comment="ID das OPs (Ordens de Produção) geradas")
    metadados = Column(Text, nullable=True, comment="Dados adicionais em formato JSON")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    formulario = relationship("Formulario", back_populates="distribuicoes")
    unidade_escolar = relationship("UnidadeEscolar", back_populates="distribuicoes")
    turma = relationship("Turma", back_populates="distribuicoes")
    especificacao = relationship("EspecificacaoForm", back_populates="distribuicoes")
    arquivo_pdf = relationship("ArquivoPdf")
    status_deskflow = relationship("StatusDeskflowPedido", back_populates="distribuicoes")
    orcamento_api = relationship("OrcamentoAPI", back_populates="distribuicao_material", uselist=False)
    aprovacao_api = relationship("AprovacaoAPI", back_populates="distribuicao_material", uselist=False)
    historico_processamento = relationship("HistoricoProcessamento", back_populates="distribuicao_material")
    
    # Índices
    __table_args__ = (
        Index('idx_distribuicao_formulario', 'formulario_id'),
        Index('idx_distribuicao_unidade', 'unidade_escolar_id'),
        Index('idx_distribuicao_turma', 'id_turma'),
        Index('idx_distribuicao_especificacao', 'especificacao_form_id'),
        Index('idx_distribuicao_status', 'status_distribuicao'),
        Index('idx_distribuicao_entrega', 'data_previsao_entrega'),
        Index('idx_distribuicao_lote', 'lote_producao'),
    )
    
    def __repr__(self):
        return f"<DistribuicaoMaterial(id={self.id}, formulario_id={self.formulario_id}, status={self.status_distribuicao})>"
