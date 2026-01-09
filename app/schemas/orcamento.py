from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import date


class OrcamentoRequest(BaseModel):
    """Request para gerar orçamento"""
    escola_id: int = Field(..., gt=0, description="ID da escola")
    ids_produtos: List[int] = Field(..., min_length=1, description="Lista de IDs de produtos")
    datas_saida: List[date] = Field(..., min_length=1, description="Lista de datas de saída")
    divisoes_logistica: Optional[List[str]] = Field(None, description="Lista de divisões logísticas (opcional)")
    dias_uteis_filtro: Optional[List[int]] = Field(None, description="Lista de dias úteis (opcional)")


class ComponenteInfo(BaseModel):
    """Informações do componente"""
    id: int
    descricao: str
    altura: Optional[float] = None
    largura: Optional[float] = None
    quantidade_paginas: Optional[int] = None
    gramaturasubstratoimpressao: Optional[float] = None
    corfrente: Optional[str] = None
    corverso: Optional[str] = None
    perguntas_componente: List[dict] = []


class PerguntaGeral(BaseModel):
    """Pergunta geral"""
    tipo: str
    pergunta: str
    resposta: Optional[str] = None
    id_pergunta: int


class ItemOrcamento(BaseModel):
    """Item do orçamento"""
    id_produto: int
    descricao: str
    quantidade: int
    usar_listapreco: int = 1
    manter_estrutura_mod_produto: int = 1
    componentes: List[ComponenteInfo] = []
    perguntas_gerais: List[PerguntaGeral] = []


class OrcamentoData(BaseModel):
    """Dados do orçamento"""
    id_cliente: Optional[int] = None
    id_vendedor: int = 2285
    id_forma_pagamento: str = "11"
    itens: List[ItemOrcamento] = []


class OrcamentoResponse(BaseModel):
    """Response com orçamento gerado"""
    identifier: str = "PageFlow"
    data: OrcamentoData


class OrcamentoListResponse(BaseModel):
    """Response com lista de orçamentos (um por unidade)"""
    orcamentos: List[OrcamentoResponse]
    total_unidades: int
    arquivo: Optional[str] = None
    mensagem: str = "Orçamento gerado com sucesso"
