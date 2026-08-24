from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.config.database import Base


class OrcamentoProcessamento(Base):
    """Espelho SQLAlchemy do model Sequelize ``OrcamentoProcessamento``.

    A tabela e sua FK para ``integra_pedidos.id`` são administradas pelo
    PAGEFLOW/Sequelize. Este model não é responsável pela criação do schema.
    """

    __tablename__ = "integra_orcamento_processamentos"

    pedido_id = Column(
        Integer,
        primary_key=True,
        nullable=False,
        comment="FK para integra_pedidos.id (1:1), gerenciada no banco pelo Sequelize",
    )
    status = Column(String(50), nullable=False)
    id_orcamento = Column(
        Integer,
        nullable=True,
        comment="ID do orçamento retornado pela API Bremen",
    )
    itens_orcamento = Column(JSONB, nullable=True)
    resposta_orcamento = Column(JSONB, nullable=True)
    resposta_aprovacao = Column(JSONB, nullable=True)
    ops = Column(JSONB, nullable=True)
    arquivos = Column(JSONB, nullable=True)
    erro = Column(Text, nullable=True)
    criado_em = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
    )
    atualizado_em = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
    )

    def to_dict(self):
        return {
            "pedido_id": self.pedido_id,
            "status": self.status,
            "id_orcamento": self.id_orcamento,
            "itens_orcamento": self.itens_orcamento,
            "resposta_orcamento": self.resposta_orcamento,
            "resposta_aprovacao": self.resposta_aprovacao,
            "ops": self.ops,
            "arquivos": self.arquivos,
            "erro": self.erro,
            "criado_em": self.criado_em,
            "atualizado_em": self.atualizado_em,
        }

    def __repr__(self):
        return (
            f"<OrcamentoProcessamento(pedido_id={self.pedido_id}, "
            f"status={self.status}, id_orcamento={self.id_orcamento})>"
        )
