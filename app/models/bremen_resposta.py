from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenResposta(Base):
    """
    Model para respostas Bremen
    
    Representa uma resposta associada a uma pergunta Bremen.
    """
    __tablename__ = "bremen_respostas"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_resposta = Column(
        Integer,
        nullable=True,
        comment="ID único da resposta para referência externa"
    )
    pergunta_id = Column(
        Integer,
        ForeignKey('bremen_perguntas.id', ondelete='SET NULL'),
        nullable=True,
        comment="FK para pergunta Bremen"
    )
    valor = Column(
        String(100),
        nullable=False,
        comment="Valor da resposta"
    )
    descricao_opcao = Column(
        Text,
        nullable=True,
        comment="Descrição textual da opção selecionada (ex.: descricao_opcao vindo do catálogo)"
    )
    
    # Relacionamentos
    pergunta = relationship(
        "BremenPergunta",
        back_populates="respostas",
        foreign_keys=[pergunta_id]
    )
    
    detalhes = relationship(
        "BremenEspecificacaoDetalhe",
        back_populates="resposta",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<BremenResposta(id={self.id}, id_resposta={self.id_resposta}, valor='{self.valor}')>"
