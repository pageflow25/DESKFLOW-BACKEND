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
    
    # Especificações de produção
    formato = Column(String(100), nullable=True, comment="Formato do item")
    cor_impressao = Column(String(100), nullable=True, comment="Cor da impressão")
    impressao = Column(String(100), nullable=True, comment="Tipo de impressão")
    gramatura = Column(String(50), nullable=True, comment="Gramatura do papel")
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
    livreto = Column(String(100), nullable=True, comment="Indica se o item possui livreto ou informações do livreto")
    corte = Column(String(100), nullable=True, comment="Tipo de corte ou acabamento especial do item")
    altura = Column(String(50), nullable=True, comment="Altura do item em milímetros")
    largura = Column(String(50), nullable=True, comment="Largura do item em milímetros")
    gramatura_miolo = Column(String(50), nullable=True, comment="Gramatura do miolo informada no formulário")
    
    # Campos específicos do modelo comercial
    origem_dados = Column(String(100), nullable=True, comment="Origem dos dados (manual, importado, etc.)")
    grupos_vinculados = Column(Text, nullable=True, comment="JSON com os grupos vinculados e seus arquivos")
    metadados = Column(Text, nullable=True, comment="Dados adicionais em formato JSON")
    
    # Relacionamento com Bremen
    id_produto = Column(
        Integer, 
        nullable=True, 
        index=True,
        comment="FK para bremen_itens.id_produto - relacionamento lógico com BremenItem"
    )
    
    # Relacionamento com gramatura e papel
    id_gramatura = Column(
        Integer,
        ForeignKey('bremen_gramatura.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="FK para bremen_gramatura"
    )
    
    id_papel = Column(
        Integer,
        ForeignKey('bremen_tamanho_papel.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="FK para bremen_tamanho_papel"
    )
    
    # Chave estrangeira para formulário
    formulario_id = Column(
        Integer, 
        ForeignKey('formularios.id', ondelete='CASCADE', onupdate='CASCADE'), 
        nullable=False,
        index=True,
        comment="ID do formulário ao qual esta especificação pertence"
    )
    
    # Timestamps
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
    bremen_item = relationship(
        "BremenItem",
        # Mark EspecificacaoForm.id_produto as the foreign column to disambiguate direction
        primaryjoin="foreign(EspecificacaoForm.id_produto) == BremenItem.id_produto",
        remote_side="BremenItem.id_produto",
        back_populates="especificacoes",
        foreign_keys="EspecificacaoForm.id_produto",
        viewonly=True,
        uselist=False
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
    
    def __repr__(self):
        return f"<EspecificacaoForm(id={self.id}, nome_item='{self.nome_item}', formulario_id={self.formulario_id})>"
