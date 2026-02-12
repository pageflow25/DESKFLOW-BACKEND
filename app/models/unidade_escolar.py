from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Date, DECIMAL, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base
import enum

class TipoUnidade(str, enum.Enum):
    """Enum para tipos de unidade escolar"""
    SEDE = "sede"
    FILIAL = "filial"
    CAMPUS = "campus"
    ANEXO = "anexo"
    POLO = "polo"

class StatusUnidade(str, enum.Enum):
    """Enum para status de unidade"""
    ATIVO = "ativo"
    INATIVO = "inativo"
    EM_CONSTRUCAO = "em_construcao"
    REFORMANDO = "reformando"

class UnidadeEscolar(Base):
    """
    Modelo UnidadeEscolar
    Representa as unidades/filiais de uma escola
    """
    __tablename__ = "unidades_escolares"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False, comment="Nome da unidade escolar")
    codigo = Column(String(50), nullable=True, comment="Código único da unidade (opcional)")
    tipo_unidade = Column(SQLEnum(TipoUnidade), nullable=False, default=TipoUnidade.SEDE, comment="Tipo da unidade escolar")
    endereco = Column(Text, nullable=True, comment="Endereço completo da unidade")
    numero = Column(String(20), nullable=True, comment="Número do endereço")
    complemento = Column(String(100), nullable=True, comment="Complemento do endereço")
    bairro = Column(String(100), nullable=True, comment="Bairro da unidade")
    cidade = Column(String(100), nullable=True, comment="Cidade da unidade")
    estado = Column(String(2), nullable=True, comment="Estado (UF) da unidade")
    cep = Column(String(10), nullable=True, comment="CEP da unidade")
    telefone = Column(String(20), nullable=True, comment="Telefone da unidade")
    email = Column(String(255), nullable=True, comment="Email da unidade")
    responsavel_nome = Column(String(255), nullable=True, comment="Nome do responsável/coordenador da unidade")
    responsavel_email = Column(String(255), nullable=True, comment="Email do responsável da unidade")
    responsavel_telefone = Column(String(20), nullable=True, comment="Telefone do responsável da unidade")
    turno_funcionamento = Column(String(100), nullable=True, comment="Turnos de funcionamento (matutino, vespertino, noturno, integral)")
    capacidade_alunos = Column(Integer, nullable=True, comment="Capacidade máxima de alunos da unidade")
    numero_salas = Column(Integer, nullable=True, comment="Número de salas de aula")
    niveis_ensino = Column(Text, nullable=True, comment="Níveis de ensino oferecidos (JSON ou texto separado por vírgula)")
    status = Column(SQLEnum(StatusUnidade), nullable=False, default=StatusUnidade.ATIVO, comment="Status da unidade")
    data_inauguracao = Column(Date, nullable=True, comment="Data de inauguração da unidade")
    observacoes = Column(Text, nullable=True, comment="Observações sobre a unidade")
    escola_id = Column(Integer, ForeignKey('escolas.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False, comment="ID da escola à qual a unidade pertence")
    coordenadas_latitude = Column(DECIMAL(10, 8), nullable=True, comment="Latitude da localização da unidade")
    coordenadas_longitude = Column(DECIMAL(11, 8), nullable=True, comment="Longitude da localização da unidade")
    divisao_logistica = Column(String(100), nullable=True, comment="Divisão logística responsável pela unidade")
    dias_uteis = Column(Integer, nullable=True, comment="Dias úteis de funcionamento da unidade (número inteiro)")
    forma_pagamento = Column(Integer, nullable=True, comment="Forma de pagamento associada à unidade escolar")
    cliente_id = Column(Integer, nullable=True, comment="ID do cliente associado à unidade escolar")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    escola = relationship("Escola", back_populates="unidades_escolares")
    distribuicoes = relationship("DistribuicaoMaterial", back_populates="unidade_escolar")
    turmas = relationship("Turma", back_populates="unidade_escolar")
    
    def __repr__(self):
        return f"<UnidadeEscolar(id={self.id}, nome='{self.nome}', escola_id={self.escola_id})>"
