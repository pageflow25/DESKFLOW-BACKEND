from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenGramaturaCapa(Base):
    """
    Model para gramaturas de capa Bremen
    
    Representa as gramaturas de papel disponíveis especificamente para capas de produtos Bremen.
    """
    __tablename__ = "bremen_gramatura_capa"
    
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
        back_populates="gramaturas_capa",
        foreign_keys=[id_item]
    )
    
    # Índices
    __table_args__ = (
        Index('idx_bremen_gramatura_capa_id_item', 'id_item'),
        Index('idx_bremen_gramatura_capa_valor', 'gramatura'),
        Index(
            'uniq_bremen_gramatura_capa_item_valor',
            'id_item',
            'gramatura',
            'unidade_medida',
            unique=True
        ),
    )
    
    def __repr__(self):
        return f"<BremenGramaturaCapa(id={self.id}, gramatura={self.gramatura} {self.unidade_medida})>"
