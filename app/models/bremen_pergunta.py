from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenPergunta(Base):
    """
    Model para perguntas Bremen
    
    Representa uma pergunta associada a componentes ou itens Bremen.
    Pode estar vinculada a um componente específico ou a um item geral.
    """
    __tablename__ = "bremen_perguntas"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_pergunta = Column(
        Integer, 
        nullable=False,
        comment="ID único da pergunta para referência externa"
    )
    id_componente = Column(
        Integer,
        ForeignKey('bremen_componentes.id_componente', ondelete='CASCADE'),
        nullable=True,
        comment="FK para componente Bremen"
    )
    id_geral = Column(
        Integer,
        ForeignKey('bremen_itens.id_produto', ondelete='SET NULL'),
        nullable=True,
        comment="FK para bremen_itens.id_produto - mapeamento geral entre item e pergunta"
    )
    nome = Column(
        String(100),
        nullable=False,
        comment="Nome da pergunta"
    )
    tipo = Column(
        Text,
        nullable=True,
        comment="Tipo da pergunta (ex: texto, múltipla escolha, etc.)"
    )
    
    # Relacionamentos
    componente = relationship(
        "BremenComponente",
        back_populates="perguntas",
        foreign_keys=[id_componente]
    )
    
    item = relationship(
        "BremenItem",
        back_populates="perguntas",
        foreign_keys=[id_geral]
    )
    
    respostas = relationship(
        "BremenResposta",
        back_populates="pergunta",
        cascade="all, delete-orphan"
    )
    
    detalhes = relationship(
        "BremenEspecificacaoDetalhe",
        back_populates="pergunta",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<BremenPergunta(id={self.id}, id_pergunta={self.id_pergunta}, nome='{self.nome}')>"
