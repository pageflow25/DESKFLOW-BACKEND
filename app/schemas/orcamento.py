from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import date


# ============================================
# Schemas para novo fluxo de orçamento
# ============================================

class ComponenteInfo(BaseModel):
    """Informações do componente"""
    id: int
    descricao: str
    altura: Optional[float] = None
    largura: Optional[float] = None
    quantidade_paginas: Optional[int] = None
    gramaturasubstratoimpressao: Optional[float] = None
    corfrente: Optional[int] = None
    corverso: Optional[int] = None
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


class EnviarOrcamentoRequest(BaseModel):
    """Request para processar workflow completo de orçamento"""
    escola_id: int = Field(..., gt=0, description="ID da escola")
    ids_produtos: List[int] = Field(..., min_length=1, description="Lista de IDs de produtos")
    datas_saida: List[date] = Field(..., min_length=1, description="Lista de datas de saída")
    divisoes_logistica: Optional[List[str]] = Field(None, description="Divisões logísticas (opcional)")
    dias_uteis_filtro: Optional[List[int]] = Field(None, description="Dias úteis (opcional)")
    aprovar_automaticamente: bool = Field(False, description="Se deve aprovar a proposta automaticamente")
    data_entrega: Optional[str] = Field(None, description="Data de entrega para aprovação (ISO format)")


class ProcessamentoResultado(BaseModel):
    """Resultado do processamento de orçamentos"""
    total: int = Field(description="Total de distribuições encontradas")
    enviados: int = Field(description="Número de orçamentos enviados para API")
    aprovados: int = Field(description="Número de orçamentos aprovados")
    salvos: int = Field(description="Número de registros salvos no banco")
    erros: List[str] = Field(default_factory=list, description="Lista de erros encontrados")
    detalhes: List[dict] = Field(default_factory=list, description="Detalhes do processamento de cada distribuição")


# ============================================
# Schemas para respostas da API externa
# ============================================

class ItemAPIResponse(BaseModel):
    """Item retornado pela API externa"""
    id: int
    data_entrega: Optional[str] = None


class OrcamentoAPIData(BaseModel):
    """Dados retornados pela API de orçamento"""
    id_orcamento: int
    gerar_op: Optional[bool] = None
    itens: List[ItemAPIResponse] = []


class OrcamentoAPIResponse(BaseModel):
    """Response da API externa de orçamento"""
    identifier: str = "PageFlow"
    data: OrcamentoAPIData

