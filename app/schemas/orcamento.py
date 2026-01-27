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


# ============================================
# Schemas para integração com API externa
# ============================================

class EnviarOrcamentoRequest(BaseModel):
    """Request para enviar orçamento à API externa"""
    escola_id: int = Field(..., gt=0, description="ID da escola")
    ids_produtos: List[int] = Field(..., min_length=1, description="Lista de IDs de produtos")
    datas_saida: List[date] = Field(..., min_length=1, description="Lista de datas de saída")
    divisoes_logistica: Optional[List[str]] = Field(None, description="Divisões logísticas (opcional)")
    dias_uteis_filtro: Optional[List[int]] = Field(None, description="Dias úteis (opcional)")
    aprovar_automaticamente: bool = Field(False, description="Se deve aprovar a proposta automaticamente")
    data_entrega: Optional[str] = Field(None, description="Data de entrega para aprovação (ISO format)")


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


class ProcessamentoResultado(BaseModel):
    """Resultado do processamento de orçamentos"""
    total: int
    enviados: int
    aprovados: int
    salvos: int
    erros: List[str] = []
    detalhes: List[dict] = []


class AprovacaoRequest(BaseModel):
    """Request para aprovação manual de orçamento"""
    data_entrega: str = Field(..., description="Data de entrega no formato ISO (ex: 2026-01-15T12:00:00.000-03:00)")


class StatusDistribuicaoResponse(BaseModel):
    """Response com status de uma distribuição"""
    distribuicao_id: int
    unidade_escolar_id: int
    unidade_nome: str
    item_nome: Optional[str] = None
    quantidade: int
    status_codigo: Optional[str] = None
    status_descricao: Optional[str] = None
    status_distribuicao: str
    id_orcamento: Optional[int] = None
    id_ops: Optional[int] = None
    tem_orcamento: bool
    foi_aprovado: bool


class StatusEscolaResponse(BaseModel):
    """Response com status geral da escola"""
    escola_id: int
    total_distribuicoes: int
    distribuicoes: List[StatusDistribuicaoResponse]


class FluxoOrcamentoRequest(BaseModel):
    """Request para definir o fluxo de processamento"""
    tipo_fluxo: str = Field(..., description="Tipo do fluxo: 'com_distribuicao_sem_faturamento' ou 'outro'")
    escola_id: int = Field(..., gt=0, description="ID da escola")
    ids_produtos: List[int] = Field(..., min_length=1, description="Lista de IDs de produtos")
    datas_saida: List[date] = Field(..., min_length=1, description="Lista de datas de saída")
    divisoes_logistica: Optional[List[str]] = Field(None, description="Divisões logísticas (opcional)")
    dias_uteis_filtro: Optional[List[int]] = Field(None, description="Dias úteis (opcional)")
    aprovar_automaticamente: bool = Field(False, description="Se deve aprovar automaticamente")
    data_entrega: Optional[str] = Field(None, description="Data de entrega para aprovação (ISO format)")

