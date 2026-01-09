from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from app.config.database import Base

class CalcBremenEscola(Base):
    """
    Model para CalcBremenEscola
    Representa os scripts de cálculo gerados para o sistema Bremen Escola.
    """
    __tablename__ = "calc_bremen_escola"
    
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="ID único do script"
    )
    nome_lista = Column(
        String(255),
        nullable=False,
        comment="Nome da lista de preços"
    )
    id_grupo = Column(
        Text,
        nullable=False,
        comment="IDs dos grupos de itens (formato: [id1,id2][id3,id4])"
    )
    tipos_papel = Column(
        Text,
        nullable=False,
        comment="Tipos de papel separados por vírgula"
    )
    valores = Column(
        Text,
        nullable=False,
        comment="Valores das variáveis para cálculo"
    )
    script_gerado = Column(
        Text,
        nullable=False,
        comment="Script final gerado"
    )
    usuario_id = Column(
        Integer,
        nullable=True,
        comment="ID do usuário que gerou o cálculo"
    )
    usuario_nome = Column(
        String(255),
        nullable=True,
        comment="Nome do usuário que gerou o cálculo"
    )
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="Data de criação do registro"
    )
    
    def __repr__(self):
        return f"<CalcBremenEscola(id={self.id}, nome_lista='{self.nome_lista}', usuario='{self.usuario_nome}')>"
