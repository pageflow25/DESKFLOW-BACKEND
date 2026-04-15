from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.orm import relationship
from app.config.database import Base


class BremenTarefa(Base):
    """
    Model para tarefas do sistema Bremen.

    Representa uma tarefa que pode ser aplicada a um componente (id_componente=True)
    ou ao fluxo geral (id_geral=True). Os dois campos são mutuamente exclusivos.
    """
    __tablename__ = "bremen_tarefas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    id_tarefa = Column(
        Integer,
        nullable=False,
        comment="ID da tarefa no sistema Bremen (referência externa)"
    )
    id_componente = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Escopo da tarefa: true quando aplicável a componente"
    )
    id_geral = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Escopo da tarefa: true quando aplicável ao fluxo geral"
    )
    descricao = Column(
        String(255),
        nullable=False,
        comment="Descrição da tarefa (ex: Arte Final / Editoração)"
    )
    descricao_pf = Column(
        String(255),
        nullable=True,
        comment="Descrição exibida no PF (nome do corte)"
    )

    # Relacionamentos
    especificacao_tarefas = relationship(
        "BremenEspecificacaoTarefa",
        back_populates="tarefa",
        cascade="all, delete-orphan"
    )
