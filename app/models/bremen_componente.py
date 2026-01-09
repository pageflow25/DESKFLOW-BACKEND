from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenComponente(Base):
    """
    Model para componentes de itens Bremen
    
    Representa um componente dentro de um item Bremen, contendo informações
    específicas sobre cada componente do produto.
    Relacionamento com bremen_itens via id_produto para ligar componentes
    ao produto do item e não ao registro do item em si.
    """
    __tablename__ = "bremen_componentes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_produto = Column(
        Integer, 
        ForeignKey('bremen_itens.id_produto', ondelete='CASCADE'), 
        nullable=False,
        comment="Relacionamento com id_produto de bremen_itens"
    )
    id_componente = Column(
        Integer, 
        nullable=False, 
        unique=True,
        comment="ID único do componente"
    )
    descricao = Column(String(255), nullable=True, comment="Descrição do componente")
    is_capa = Column(
        Boolean, 
        nullable=True, 
        default=False,
        comment="Indica se o componente é uma capa"
    )
    is_miolo = Column(
        Boolean, 
        nullable=True, 
        default=False,
        comment="Indica se o componente é um miolo"
    )
    
    # Relacionamentos
    item = relationship(
        "BremenItem", 
        back_populates="componentes",
        foreign_keys=[id_produto]
    )
    
    arquivos = relationship(
        "ArquivoPdf",
        back_populates="componente",
        foreign_keys="ArquivoPdf.id_componente",
        cascade="all, delete-orphan"
    )
    
    perguntas = relationship(
        "BremenPergunta",
        back_populates="componente",
        foreign_keys="BremenPergunta.id_componente",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<BremenComponente(id={self.id}, id_produto={self.id_produto}, id_componente={self.id_componente})>"
