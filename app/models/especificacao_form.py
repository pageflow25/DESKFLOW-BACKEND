from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship, foreign
from sqlalchemy.sql import func
from app.config.database import Base

class EspecificacaoForm(Base):
    """
    Model para especificações de itens do formulário
    
    Este model representa um item específico dentro de um pedido/formulário,
    permitindo que um único pedido tenha múltiplos itens com especificações diferentes.
    
    Exemplo: Uma apostila pode ter:
    - Item 1: Capa (papel couché, colorido)
    - Item 2: Miolo P&B (papel offset, gramatura 75g)
    - Item 3: Miolo Colorido (papel couché, gramatura 90g)
    """
    __tablename__ = "especificacoes_form"
    
    id = Column(Integer, primary_key=True, index=True)
    nome_item = Column(String(255), nullable=True, comment="Nome descritivo do item (ex: Capa e Contracapa, Miolo Colorido, Miolo P&B)")
    quantidade = Column(Integer, nullable=False, default=1, comment="Quantidade do item")
    grupo = Column(String(100), nullable=True, comment="Agrupamento lógico dos itens (principal, unidade, capa, miolo, etc.)")
    
    # Relacionamento com produto
    id_produto = Column(
        Integer,
        nullable=True,
        comment="FK para bremen_itens.id_produto"
    )
    
    # Especificações de tamanho
    largura = Column(String(50), nullable=True, comment="Largura do item")
    altura = Column(String(50), nullable=True, comment="Altura do item")
    
    # Especificações de material
    gramatura_miolo = Column(String(50), nullable=True, comment="Gramatura do miolo")
    id_gramatura = Column(
        Integer,
        ForeignKey('bremen_gramatura.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="FK para tabela de gramaturas catalogadas"
    )
    id_papel = Column(
        Integer,
        ForeignKey('bremen_tamanho_papel.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        comment="FK para tamanhos de papel"
    )
    
    # Cores
    corfrente = Column(Integer, nullable=True, comment="Número de cores da frente")
    corverso = Column(Integer, nullable=True, comment="Número de cores do verso")
    
    # Especificações de produção
    formato = Column(String(100), nullable=True, comment="Formato do item")
    cor_impressao = Column(String(100), nullable=True, comment="Cor da impressão")
    impressao = Column(String(100), nullable=True, comment="Tipo de impressão")
    papel_adesivo = Column(Boolean, default=False, nullable=True, comment="Se utiliza papel adesivo")
    tipo_adesivo = Column(String(100), nullable=True, comment="Tipo do adesivo")
    grampos = Column(String(100), nullable=True, comment="Tipo de grampos")
    espiral = Column(Boolean, default=False, nullable=True, comment="Se possui espiral")
    capa_pvc = Column(Boolean, default=False, nullable=True, comment="Se possui capa PVC")
    cod_op = Column(String(50), nullable=True, comment="Código da operação")
    formato_final = Column(String(100), nullable=True, comment="Formato final do produto")
    tipo_capa = Column(String(100), nullable=True, comment="Tipo da capa")
    cor_acabamento = Column(String(100), nullable=True, comment="Cor do acabamento")
    acabamento = Column(String(100), nullable=True, comment="Tipo de acabamento")
    tipo_papel = Column(String(100), nullable=True, comment="Tipo do papel")
    laminacao = Column(String(100), nullable=True, comment="Tipo de laminação")
    
    # Metadados
    metadados = Column(Text, nullable=True, comment="Dados adicionais em formato JSON")
    obs = Column(Text, nullable=True, comment="Observações gerais")
    
    # Relacionamentos com tabelas
    formulario_id = Column(
        Integer,
        ForeignKey('formularios.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=True
    )
    
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    formulario = relationship("Formulario", back_populates="especificacoes")
    
    # Uma especificação pode ter múltiplos arquivos PDF
    arquivos = relationship(
        "ArquivoPdf", 
        back_populates="especificacao_form",
        foreign_keys="ArquivoPdf.item_pedido_id",
        cascade="all, delete-orphan"
    )
    
    # Uma especificação pode ter múltiplas distribuições de materiais
    distribuicoes = relationship(
        "DistribuicaoMaterial",
        back_populates="especificacao",
        foreign_keys="DistribuicaoMaterial.especificacao_form_id"
    )
    
    # Relacionamento lógico (sem FK) com BremenItem via id_produto
    item = relationship(
        "BremenItem",
        primaryjoin="foreign(EspecificacaoForm.id_produto) == BremenItem.id_produto",
        back_populates="especificacoes",
        foreign_keys=[id_produto],
        viewonly=True
    )
    
    # Relacionamentos com gramatura e papel
    gramatura_obj = relationship(
        "BremenGramatura",
        back_populates="especificacoes",
        foreign_keys=[id_gramatura]
    )
    
    papel_obj = relationship(
        "BremenTamanhoPapel",
        back_populates="especificacoes",
        foreign_keys=[id_papel]
    )
    
    # Relacionamento com detalhes de especificação (perguntas e respostas)
    detalhes = relationship(
        "BremenEspecificacaoDetalhe",
        back_populates="especificacao",
        cascade="all, delete-orphan"
    )

    # Relacionamento N:N com bremen_tarefas via tabela pivot
    especificacao_tarefas = relationship(
        "BremenEspecificacaoTarefa",
        back_populates="especificacao",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<EspecificacaoForm(id={self.id}, nome_item='{self.nome_item}', formulario_id={self.formulario_id})>"
