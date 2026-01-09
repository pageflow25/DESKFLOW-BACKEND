from sqlalchemy import Column, Integer, String, ForeignKey, Index, Boolean, Text
from sqlalchemy.orm import relationship, foreign
from app.config.database import Base

class BremenItem(Base):
    """
    Model para itens de pedidos Bremen
    
    Representa um item dentro de um pedido Bremen, contendo informações sobre
    o produto, descrição e quantidade. O campo `id_produto` serve como referência
    para vincular com especificações de formulários (EspecificacaoForm).
    
    Nota: O relacionamento com EspecificacaoForm é feito via id_produto, mas não
    há uma chave estrangeira explícita no banco de dados para manter flexibilidade.
    """
    __tablename__ = "bremen_itens"
    
    # Colunas principais
    id = Column(Integer, primary_key=True, autoincrement=True, comment="Chave primária do item Bremen")
    id_produto = Column(
        Integer, 
        nullable=False, 
        index=True,
        comment="ID único do produto Bremen - usado como referência por especificacoes_form.id_produto e componentes"
    )
    descricao = Column(
        String(255), 
        nullable=True, 
        comment="Descrição textual do produto/item"
    )
    categoria_prod = Column(
        String(50),
        nullable=True,
        name='categoria_Prod',
        comment="Categoria do produto: 'Apostila', 'Livreto', 'Avulso', 'Prova'"
    )
    sub_grupo = Column(
        String(100),
        nullable=True,
        comment="Subgrupo administrativo vinculado ao item"
    )
    is_ativo = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Indica se o item está ativo e deve ser exibido nas listagens"
    )
    is_conveniado = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Indica se o item é exclusivo para formulários de escolas conveniadas"
    )
    frente_verso = Column(
        Text,
        nullable=True,
        default=None,
        comment="Indica frente/verso (texto, pode ser null)"
    )
    
    # Relacionamentos
    componentes = relationship(
        "BremenComponente", 
        back_populates="item",
        foreign_keys="BremenComponente.id_produto",
        primaryjoin="BremenItem.id_produto == BremenComponente.id_produto",
        cascade="all, delete-orphan",
        lazy="select"
    )
    
    # Relacionamento bidirecional com EspecificacaoForm via id_produto
    # IMPORTANTE: Este é um relacionamento lógico, não há FK no banco
    # viewonly=True indica que SQLAlchemy não deve tentar sincronizar mudanças
    especificacoes = relationship(
        "EspecificacaoForm",
        primaryjoin="BremenItem.id_produto == foreign(EspecificacaoForm.id_produto)",
        back_populates="bremen_item",
        foreign_keys="EspecificacaoForm.id_produto",
        viewonly=True,
        lazy="select"
    )
    
    # Relacionamento com perguntas via id_produto
    perguntas = relationship(
        "BremenPergunta",
        back_populates="item",
        foreign_keys="BremenPergunta.id_geral",
        primaryjoin="BremenItem.id_produto == BremenPergunta.id_geral",
        lazy="select"
    )
    
    # Relacionamento com gramaturas via id_produto
    gramaturas = relationship(
        "BremenGramatura",
        back_populates="item",
        foreign_keys="BremenGramatura.id_item",
        primaryjoin="BremenItem.id_produto == BremenGramatura.id_item",
        lazy="select"
    )
    
    # Índices compostos para otimizar queries
    __table_args__ = (
        Index('idx_bremen_itens_id_produto', 'id_produto'),
    )
    
    def __repr__(self):
        return f"<BremenItem(id={self.id}, id_produto={self.id_produto}, descricao='{self.descricao[:30] if self.descricao else 'N/A'}', categoria='{self.categoria_prod}')>"
    
    def to_dict(self):
        """Converte o objeto para dicionário para serialização"""
        return {
            'id': self.id,
            'id_produto': self.id_produto,
            'descricao': self.descricao,
            'categoria_prod': self.categoria_prod,
            'sub_grupo': self.sub_grupo,
            'is_ativo': self.is_ativo,
            'is_conveniado': self.is_conveniado,
            'frente_verso': self.frente_verso
        }
