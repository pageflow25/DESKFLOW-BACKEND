from sqlalchemy import Column, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.config.database import Base

class BremenEspecificacaoDetalhe(Base):
    """
    Model BremenEspecificacaoDetalhe
    Representa os detalhes de uma especificação através de perguntas e respostas.
    Relaciona especificacoes_form com bremen_perguntas e bremen_respostas.
    """
    __tablename__ = "bremen_especificacao_detalhes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    especificacao_id = Column(
        Integer,
        ForeignKey('especificacoes_form.id', ondelete='CASCADE'),
        nullable=False,
        comment="Referência à especificação do formulário"
    )
    pergunta_id = Column(
        Integer,
        ForeignKey('bremen_perguntas.id', ondelete='CASCADE'),
        nullable=False,
        comment="Referência à pergunta"
    )
    resposta_id = Column(
        Integer,
        ForeignKey('bremen_respostas.id', ondelete='CASCADE'),
        nullable=False,
        comment="Referência à resposta escolhida"
    )
    
    # Relacionamentos
    especificacao = relationship(
        "EspecificacaoForm",
        back_populates="detalhes",
        foreign_keys=[especificacao_id]
    )
    
    pergunta = relationship(
        "BremenPergunta",
        back_populates="detalhes",
        foreign_keys=[pergunta_id]
    )
    
    resposta = relationship(
        "BremenResposta",
        back_populates="detalhes",
        foreign_keys=[resposta_id]
    )
    
    # Índice único para garantir uma resposta por pergunta por especificação
    __table_args__ = (
        Index(
            'idx_bremen_especificacao_detalhes_unique',
            'especificacao_id',
            'pergunta_id',
            unique=True
        ),
    )
    
    def __repr__(self):
        return f"<BremenEspecificacaoDetalhe(id={self.id}, especificacao_id={self.especificacao_id}, pergunta_id={self.pergunta_id})>"
