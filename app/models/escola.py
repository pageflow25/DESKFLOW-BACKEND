from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class Escola(Base):
    """
    Modelo Escola
    Representa as escolas/instituições de ensino
    """
    __tablename__ = "escolas"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False, comment="Nome da escola/instituição")
    codigo = Column(String(50), unique=True, nullable=True, comment="Código único da escola")
    cnpj = Column(String(18), nullable=True, comment="CNPJ da escola")
    endereco = Column(Text, nullable=True, comment="Endereço completo da escola")
    cidade = Column(String(100), nullable=True, comment="Cidade da escola")
    estado = Column(String(2), nullable=True, comment="Estado (UF) da escola")
    cep = Column(String(10), nullable=True, comment="CEP da escola")
    telefone = Column(String(20), nullable=True, comment="Telefone principal da escola")
    email = Column(String(255), nullable=True, comment="Email institucional da escola")
    responsavel_nome = Column(String(255), nullable=True, comment="Nome do responsável/diretor da escola")
    responsavel_email = Column(String(255), nullable=True, comment="Email do responsável/diretor")
    responsavel_telefone = Column(String(20), nullable=True, comment="Telefone do responsável/diretor")
    tipo_escola = Column(String(50), nullable=False, default='publica', comment="Tipo da escola (publica, privada, federal, estadual, municipal)")
    nivel_ensino = Column(String(100), nullable=True, comment="Nível de ensino (fundamental, médio, superior, etc.)")
    status = Column(String(50), nullable=False, default='ativo', comment="Status da escola (ativo, inativo, suspenso)")
    observacoes = Column(Text, nullable=True, comment="Observações sobre a escola")
    vendedor_id = Column(Integer, nullable=True, comment="ID do vendedor responsável pela escola")
    data_cadastro = Column(Date, nullable=False, server_default=func.now(), comment="Data de cadastro da escola")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    # Uma escola pode ter muitos usuários
    usuarios = relationship(
        "Usuario", 
        back_populates="escola",
        cascade="all, delete-orphan"
    )
    
    # Uma escola pode ter muitas unidades escolares
    unidades = relationship(
        "UnidadeEscolar",
        back_populates="escola",
        cascade="all, delete-orphan",
        foreign_keys="UnidadeEscolar.escola_id"
    )
    
    # Uma escola pode ter muitos clientes (se houver modelo Cliente)
    # Descomente se o modelo Cliente existir
    # clientes = relationship(
    #     "Cliente",
    #     back_populates="escola",
    #     foreign_keys="Cliente.escola_id"
    # )
    
    def __repr__(self):
        return f"<Escola(id={self.id}, nome='{self.nome}', codigo='{self.codigo}')>"
