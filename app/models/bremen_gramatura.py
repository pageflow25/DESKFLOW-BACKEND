from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenGramatura(Base):
    """
    Model para gramaturas Bremen
    
    Representa as gramaturas de papel disponíveis para produtos Bremen.
    """
    __tablename__ = "bremen_gramatura"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_item = Column(
        Integer,
        ForeignKey('bremen_itens.id_produto', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=True,
        comment="FK para bremen_itens.id_produto"
    )
    gramatura = Column(
        Numeric(10, 2),
        nullable=False,
        comment="Valor da gramatura"
    )
    unidade_medida = Column(
        String(20),
        nullable=False,
        default='g/m²',
        comment="Unidade de medida da gramatura"
    )
    
    # Relacionamentos
    item = relationship(
        "BremenItem",
        back_populates="gramaturas",
        foreign_keys=[id_item]
    )
    
    especificacoes = relationship(
        "EspecificacaoForm",
        back_populates="gramatura_obj",
        foreign_keys="EspecificacaoForm.id_gramatura"
    )
    
    # Índices
    __table_args__ = (
        Index('idx_bremen_gramatura_id_item', 'id_item'),
        Index('idx_bremen_gramatura_valor', 'gramatura'),
        Index(
            'uniq_bremen_gramatura_item_valor',
            'id_item',
            'gramatura',
            'unidade_medida',
            unique=True
        ),
    )
    
    def __repr__(self):
        return f"<BremenGramatura(id={self.id}, gramatura={self.gramatura} {self.unidade_medida})>"
