from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class Formulario(Base):
    __tablename__ = "formularios"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=True)
    email = Column(String, nullable=True)
    titulo = Column(String, nullable=True)
    data_entrega = Column(Date, nullable=True)
    observacoes = Column(Text, nullable=True)
    nome_cliente = Column(String, nullable=True)
    responsavel_pedido = Column(String, nullable=True)
    # data_saida está na tabela distribuicao_materiais, não em formularios
    path_destino_de_arquivos = Column(String, nullable=True, comment="Caminho de destino dos arquivos relacionados ao formulário")
    tipo_formulario = Column(String, nullable=False, default="desconhecido", comment="Tipo do formulário (zerohum, comercial, etc.)")
    modelo = Column(String(255), nullable=True, comment="Modelo/tipo do formulário selecionado no Bremen (ex: Prova Digital A4, Apostila 75g 1x1)")
    cliente_id = Column(Integer, nullable=True)  # Removido FK para flexibilidade
    status_formulario_id = Column(Integer, nullable=True)  # Removido FK para flexibilidade
    usuario_id = Column(Integer, nullable=True)  # Removido FK para flexibilidade
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    # usuario = relationship("Usuario", back_populates="formularios")
    arquivo_pdfs = relationship("ArquivoPdf", back_populates="formulario")
    # status_formulario = relationship("StatusFormulario")
    especificacoes = relationship("EspecificacaoForm", back_populates="formulario", cascade="all, delete-orphan")
    distribuicoes = relationship("DistribuicaoMaterial", back_populates="formulario", cascade="all, delete-orphan")
