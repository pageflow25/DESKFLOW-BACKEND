from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Date, DECIMAL
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

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
    status_distribuicao = Column(String(50), nullable=True, comment="Status da distribuição para esta unidade")
    data_previsao_entrega = Column(DateTime, nullable=True, comment="Data prevista para entrega na unidade")
    data_saida = Column(Date, nullable=True, comment="Data de saída do material para distribuição")
    endereco_entrega = Column(Text, nullable=True, comment="Endereço específico para entrega")
    responsavel_recebimento = Column(String(255), nullable=True, comment="Nome do responsável que recebeu o material")
    telefone_contato = Column(String(20), nullable=True, comment="Telefone do contato para entrega")
    codigo_rastreamento = Column(String(100), nullable=True, comment="Código de rastreamento da entrega")
    valor_unitario = Column(DECIMAL(10, 2), nullable=True, comment="Valor unitário do material")
    valor_total = Column(DECIMAL(10, 2), nullable=True, comment="Valor total do material")
    ordem_producao = Column(Integer, nullable=True, comment="Número da ordem de produção")
    lote_producao = Column(String(100), nullable=True, comment="Lote de produção")
    area = Column(String(100), nullable=True, comment="Área responsável")
    nome_area = Column(String(255), nullable=True, comment="Nome da área")
    metadados = Column(Text, nullable=True, comment="Metadados adicionais em formato JSON")
    
    # Chaves estrangeiras
    arquivo_pdf_id = Column(
        Integer, 
        ForeignKey('arquivo_pdfs.id', ondelete='SET NULL', onupdate='CASCADE'), 
        nullable=True, 
        comment="ID do arquivo PDF relacionado"
    )
    
    formulario_id = Column(
        Integer,
        ForeignKey('formularios.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID do formulário ao qual esta distribuição pertence"
    )
    
    unidade_escolar_id = Column(
        Integer,
        ForeignKey('unidades_escolares.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="ID da unidade escolar que receberá o material"
    )
    
    especificacao_form_id = Column(
        Integer,
        ForeignKey('especificacoes_form.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="ID da especificação do formulário (se aplicável)"
    )
    
    id_turma = Column(
        Integer,
        ForeignKey('turmas.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="ID da turma que receberá o material (se aplicável)"
    )
    
    # Campos para integração DeskFlow
    status_id = Column(
        Integer,
        ForeignKey('status_deskflow_pedido.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="ID do status atual do processamento DeskFlow"
    )
    
    path_arquivos = Column(
        Text,
        nullable=True,
        comment="Caminho dos arquivos processados"
    )
    
    id_orcamento = Column(
        Integer,
        nullable=True,
        comment="ID do orçamento retornado pela API DeskFlow"
    )
    
    id_ops = Column(
        Integer,
        nullable=True,
        comment="ID das OPs (Ordens de Produção) geradas"
    )
    
    # Timestamps
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    formulario = relationship("Formulario", back_populates="distribuicoes")
    
    unidade_escolar = relationship(
        "UnidadeEscolar",
        back_populates="distribuicoes",
        foreign_keys=[unidade_escolar_id]
    )
    
    turma = relationship(
        "Turma",
        back_populates="distribuicoes",
        foreign_keys=[id_turma]
    )
    
    especificacao = relationship(
        "EspecificacaoForm",
        back_populates="distribuicoes",
        foreign_keys=[especificacao_form_id]
    )
    
    arquivo_pdf = relationship(
        "ArquivoPdf",
        foreign_keys=[arquivo_pdf_id]
    )
    
    status_deskflow = relationship(
        "StatusDeskflowPedido",
        back_populates="distribuicoes",
        foreign_keys=[status_id]
    )
    
    orcamento_api = relationship(
        "OrcamentoAPI",
        back_populates="distribuicao_material",
        uselist=False,
        cascade="all, delete-orphan"
    )
    
    aprovacao_api = relationship(
        "AprovacaoAPI",
        back_populates="distribuicao_material",
        uselist=False,
        cascade="all, delete-orphan"
    )
    
    historico_processamento = relationship(
        "HistoricoProcessamento",
        back_populates="distribuicao_material",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<DistribuicaoMaterial(id={self.id}, formulario_id={self.formulario_id}, quantidade={self.quantidade})>"
