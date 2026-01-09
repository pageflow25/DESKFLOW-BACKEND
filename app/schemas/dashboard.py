from pydantic import BaseModel
from typing import Optional


class EscolaListItem(BaseModel):
    """Schema para item da lista de escolas no dashboard"""
    escola_id: int
    nome_escola: str
    codigo_escola: Optional[str] = None
    total_pedidos: int

    class Config:
        from_attributes = True


class DashboardResponse(BaseModel):
    """Schema para resposta do dashboard"""
    escolas: list[EscolaListItem]
    total_escolas: int
