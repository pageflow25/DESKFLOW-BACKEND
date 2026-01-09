from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class Turma(Base):
    __tablename__ = "turmas"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False, comment="Nome da turma (ex: 1º ano A, Infantil 3, etc.)")
    descricao = Column(String(255), nullable=True, comment="Descrição adicional da turma")
    ano = Column(String(10), nullable=True, comment="Ano letivo da turma")
    turno = Column(String(50), nullable=True, comment="Turno da turma (matutino, vespertino, noturno, etc.)")
    area = Column(String(100), nullable=True, comment="Área/categoria da turma (ex: Ensino Fundamental, Infantil, etc.)")
    id_unidade_escolar = Column(Integer, ForeignKey('unidades_escolares.id', ondelete='CASCADE'), nullable=False, comment="ID da unidade escolar à qual a turma pertence")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    unidade_escolar = relationship("UnidadeEscolar", back_populates="turmas")
    distribuicoes = relationship("DistribuicaoMaterial", back_populates="turma")