from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.config.database import Base

class BremenPedido(Base):
    """
    Model para pedidos Bremen
    
    Representa um pedido no sistema Bremen, contendo informações sobre
    cliente, vendedor, forma de pagamento e observações.
    """
    __tablename__ = "bremen_pedidos"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    identifier = Column(String(50), nullable=False, default='PageFlow', comment="Identificador do sistema de origem")
    id_cliente = Column(Integer, nullable=False, comment="ID do cliente")
    id_vendedor = Column(Integer, nullable=False, comment="ID do vendedor")
    id_forma_pagamento = Column(Integer, nullable=False, comment="ID da forma de pagamento")
    data_criacao = Column(DateTime, server_default=func.now(), nullable=False, comment="Data de criação do pedido")
    observacao = Column(Text, nullable=True, comment="Observações sobre o pedido")
    
    def __repr__(self):
        return f"<BremenPedido(id={self.id}, identifier='{self.identifier}', cliente_id={self.id_cliente})>"
