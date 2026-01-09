from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=True)
    username = Column(String, unique=True, nullable=True)
    email = Column(String, unique=True, nullable=False)
    senha_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    is_ativo = Column(Boolean, default=True)
    roles = Column(String, default="usuario")
    escola_id = Column(Integer, ForeignKey('escolas.id'), nullable=True)  # FK para escola
    metadados = Column(JSONB, nullable=True, comment="Dados adicionais do usuário em formato JSON")
    emails_vinculados = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now(), nullable=False)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relacionamentos
    escola = relationship("Escola", back_populates="usuarios")
    # formularios = relationship("Formulario", back_populates="usuario")
    
    def has_role(self, role: str) -> bool:
        """Verifica se o usuário tem uma role específica"""
        if not self.roles:
            return False
        user_roles = [r.strip() for r in self.roles.split(',')]
        return role in user_roles
    
    def is_pcp_authorized(self) -> bool:
        """Verifica se o usuário tem acesso ao sistema PCP"""
        return self.has_role('gerente') or self.has_role('admin') or self.is_admin
