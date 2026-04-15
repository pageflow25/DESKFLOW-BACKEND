from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class BremenEspecificacaoTarefa(Base):
    """
    Tabela pivot N:N entre especificacoes_form e bremen_tarefas.
    Permite múltiplas tarefas por especificação (ex: corte + plastificação).
    """
    __tablename__ = "bremen_especificacao_tarefas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    especificacao_id = Column(
        Integer,
        ForeignKey("especificacoes_form.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        comment="Referência para especificacoes_form.id"
    )
    tarefa_id = Column(
        Integer,
        ForeignKey("bremen_tarefas.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        comment="Referência para bremen_tarefas.id"
    )
    criado_em = Column(
        DateTime,
        nullable=False,
        default=func.now(),
        comment="Data de criação do registro"
    )

    __table_args__ = (
        UniqueConstraint("especificacao_id", "tarefa_id", name="uq_bremen_espec_tarefa"),
        Index("idx_bet_especificacao_id", "especificacao_id"),
        Index("idx_bet_tarefa_id", "tarefa_id"),
    )

    # Relacionamentos
    especificacao = relationship(
        "EspecificacaoForm",
        back_populates="especificacao_tarefas"
    )
    tarefa = relationship(
        "BremenTarefa",
        back_populates="especificacao_tarefas"
    )
