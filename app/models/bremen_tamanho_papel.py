from sqlalchemy import Column, Integer, Numeric, Index, String
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenTamanhoPapel(Base):
    """
    Model para tamanhos de papel Bremen
    
    Representa os tamanhos de papel disponíveis (dimensões em mm).
    """
    __tablename__ = "bremen_tamanho_papel"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    largura = Column(
        Numeric(10, 2),
        nullable=False,
        comment="Largura em milímetros"
    )
    altura = Column(
        Numeric(10, 2),
        nullable=False,
        comment="Altura em milímetros"
    )
    label = Column(
        String(120),
        nullable=True,
        comment="Descrição amigável exibida no front"
    )
    
    # Relacionamentos
    especificacoes = relationship(
        "EspecificacaoForm",
        back_populates="papel_obj",
        foreign_keys="EspecificacaoForm.id_papel"
    )
    
    # Índice único para dimensões
    __table_args__ = (
        Index(
            'idx_bremen_tamanho_papel_dimensoes',
            'largura',
            'altura',
            unique=True
        ),
    )
    
    def __repr__(self):
        return f"<BremenTamanhoPapel(id={self.id}, largura={self.largura}mm, altura={self.altura}mm)>"
