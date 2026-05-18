from pydantic import BaseModel, Field
from typing import Optional, Any


class ArquivoInfo(BaseModel):
    """Informações de arquivo individual"""
    id: Optional[int] = None
    arquivo: str
    copias: int
    paginas: Optional[int] = None


class UnidadeInfo(BaseModel):
    """Informações de unidade escolar"""
    id: int
    unidade: str
    quantidade: int
    arquivos: list[ArquivoInfo]


class DataInfo(BaseModel):
    """Informações de data de saída"""
    data_saida: str
    quantidade: int
    unidades: list[UnidadeInfo]


class ProdutoInfo(BaseModel):
    """Informações de produto"""
    id_produto: int
    produto: str
    quantidade: int
    datas: list[DataInfo]


class DivisaoLogisticaInfo(BaseModel):
    """Informações de divisão logística"""
    divisao_logistica: str
    dias_uteis: str
    quantidade_total: int
    produtos: list[ProdutoInfo]


class PedidoCascataRequest(BaseModel):
    """Request para obter detalhes da escola em cascata"""
    escola_id: int = Field(..., gt=0, description="ID da escola")


class PedidoCascataResponse(BaseModel):
    """Response com dados em cascata da escola"""
    dashboard_completo: list[DivisaoLogisticaInfo]


class StatusDeskflowOption(BaseModel):
    """Opção de status para filtros da UI."""
    id: int
    codigo: str
    descricao: str


class StatusDeskflowListResponse(BaseModel):
    """Lista de status disponíveis para seleção no frontend."""
    status: list[StatusDeskflowOption]
