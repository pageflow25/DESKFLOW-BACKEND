from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class ArquivoPdf(Base):
    __tablename__ = "arquivo_pdfs"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False, comment="Nome do arquivo PDF")
    arquivo = Column(String(500), nullable=True, comment="URL de visualização do arquivo no DriveHQ")
    caminho_remoto = Column(String(500), nullable=True, comment="Caminho do arquivo no servidor DriveHQ (para exclusão)")
    tamanho = Column(Integer, nullable=True, comment="Tamanho do arquivo em bytes")
    paginas = Column(Integer, nullable=True, comment="Quantidade de páginas do arquivo PDF")
    tipo_arquivo = Column(String, nullable=True, default="miolo", comment="Tipo do arquivo (miolo, capa, etc.)")
    dimensao = Column(String(100), nullable=True, comment="Dimensão do PDF (ex: A4, A3 ou larguraxaltura em mm)")
    pares = Column(Integer, nullable=True, comment="Identificador para vincular miolos e capas - mesmo valor indica um par")
    id_componente = Column(
        Integer, 
        ForeignKey('bremen_componentes.id_componente', ondelete='SET NULL'), 
        nullable=True,
        comment="Referência ao componente Bremen associado ao arquivo PDF"
    )
    formulario_id = Column(Integer, ForeignKey('formularios.id'), nullable=True, comment="FK para formulário")
    item_pedido_id = Column(Integer, ForeignKey('especificacoes_form.id', ondelete='SET NULL', onupdate='CASCADE'), nullable=True, comment="ID da especificação relacionada")
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    formulario = relationship("Formulario", back_populates="arquivo_pdfs")
    especificacao_form = relationship("EspecificacaoForm", back_populates="arquivos", foreign_keys=[item_pedido_id])
    componente = relationship(
        "BremenComponente",
        back_populates="arquivos",
        foreign_keys=[id_componente]
    )
