from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base


class DownloadBremen(Base):
    """
    Modelo DownloadBremen
    Armazena o registro de cada arquivo PDF baixado na FASE 03 do DeskFlow.
    
    Uma linha por arquivo baixado para cada OP.
    Se um item tem capa + miolo, gera duas linhas (uma para cada arquivo).
    """
    __tablename__ = "downloads_bremen"
    
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True
    )
    
    distribuicao_material_id = Column(
        Integer,
        ForeignKey('distribuicao_materiais.id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False,
        index=True,
        comment="ID da distribuição de material vinculada a este download"
    )
    
    id_ops = Column(
        Integer,
        nullable=False,
        index=True,
        comment="Número da OP (Ordem de Produção)"
    )
    
    arquivo_pdf_id = Column(
        Integer,
        ForeignKey('arquivo_pdfs.id', ondelete='SET NULL', onupdate='CASCADE'),
        nullable=True,
        index=True,
        comment="FK para arquivo_pdfs.id"
    )
    
    tipo_arquivo = Column(
        String(50),
        nullable=True,
        comment="Tipo do arquivo: 'capa' ou 'miolo'"
    )
    
    caminho_local = Column(
        Text,
        nullable=True,
        comment="Caminho completo do arquivo salvo localmente"
    )
    
    tamanho = Column(
        Integer,
        nullable=True,
        comment="Tamanho do arquivo baixado em bytes"
    )
    
    criado_em = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="Data e hora do download"
    )
    
    # Relacionamentos
    distribuicao_material = relationship(
        "DistribuicaoMaterial",
        back_populates="downloads_bremen",
        foreign_keys=[distribuicao_material_id]
    )
    
    arquivo_pdf = relationship(
        "ArquivoPdf",
        foreign_keys=[arquivo_pdf_id]
    )
    
    # Índices
    __table_args__ = (
        Index('idx_download_bremen_ops', 'id_ops'),
        Index('idx_download_bremen_dist_ops', 'distribuicao_material_id', 'id_ops'),
    )
    
    def __repr__(self):
        return (
            f"<DownloadBremen(id={self.id}, id_ops={self.id_ops}, "
            f"tipo={self.tipo_arquivo}, arquivo_pdf_id={self.arquivo_pdf_id})>"
        )
    
    def __str__(self):
        return (
            f"Download #{self.id} - OP: {self.id_ops}, "
            f"Tipo: {self.tipo_arquivo or 'N/A'}, "
            f"Arquivo: {self.arquivo_pdf_id or 'N/A'}"
        )
