from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class OrcamentoRequest(BaseModel):
    """Request para gerar orçamento"""
    escola_id: int = Field(..., gt=0, description="ID da escola")
    ids_produtos: List[int] = Field(..., min_length=1, description="Lista de IDs de produtos")
    datas_saida: List[date] = Field(..., min_length=1, description="Lista de datas de saída")
    divisoes_logistica: Optional[List[str]] = Field(None, description="Lista de divisões logísticas (opcional)")
    dias_uteis_filtro: Optional[List[int]] = Field(None, description="Lista de dias úteis (opcional)")
    modo_agrupamento: str = Field("unidade", description="Modo de agrupamento: 'unidade' (por unidade) ou 'escola' (agrupado por escola)")


class ComponenteInfo(BaseModel):
    """Informações do componente"""
    id: int
    id_distribuicao: Optional[int] = Field(None, description="ID da distribuição de material (chave para correspondência sequencial)")
    descricao: str
    altura: Optional[float] = None
    largura: Optional[float] = None
    quantidade_paginas: Optional[int] = None
    idgruposubstratoimpressao: Optional[int] = None
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
    descricao: Optional[str] = "Sem descrição"
    quantidade: int
    usar_listapreco: int = 1
    manter_estrutura_mod_produto: int = 1
    ids_distribuicao: Optional[List[int]] = Field(None, description="IDs de distribuição agrupados (modo escola)")
    componentes: List[ComponenteInfo] = []
    perguntas_gerais: List[PerguntaGeral] = []


class OrcamentoData(BaseModel):
    """Dados do orçamento"""
    id_cliente: Optional[int] = None
    id_vendedor: Optional[int] = 2285
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


class ProcessamentoResultado(BaseModel):
    """Resultado do processamento de orçamentos"""
    total: int
    enviados: int
    aprovados: int
    salvos: int
    downloads: int = 0
    erros: List[str] = []
    detalhes: List[dict] = []


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
    baixar_arquivos: bool = Field(False, description="Se deve baixar arquivos após aprovação (FASE 03)")
    gerar_op: bool = Field(True, description="Se deve gerar OP na aprovação (FASE 02). False envia gerar_op=false para a API Bremen.")
    modo_agrupamento: str = Field("unidade", description="Modo de agrupamento: 'unidade' ou 'escola'")


class GerarOrcamentoCompleto(BaseModel):
    """Request para gerar orçamento com fluxo completo"""
    escola_id: int = Field(..., gt=0, description="ID da escola")
    ids_produtos: List[int] = Field(..., min_length=1, description="Lista de IDs de produtos")
    datas_saida: List[date] = Field(..., min_length=1, description="Lista de datas de saída")
    divisoes_logistica: Optional[List[str]] = Field(None, description="Lista de divisões logísticas (opcional)")
    dias_uteis_filtro: Optional[List[int]] = Field(None, description="Lista de dias úteis (opcional)")
    
    # Parâmetros para o fluxo completo
    executar_fluxo_completo: bool = Field(True, description="Se deve executar o fluxo completo (geração + aprovação)")
    tipo_fluxo: str = Field(default="com_distribuicao_sem_faturamento", description="Tipo do fluxo de processamento")
    aprovar_automaticamente: bool = Field(True, description="Se deve aprovar automaticamente")
    data_entrega: Optional[str] = Field(None, description="Data de entrega para aprovação (formato ISO)")
    baixar_arquivos: bool = Field(True, description="Se deve baixar arquivos após aprovação (FASE 03)")
    gerar_op: bool = Field(True, description="Se deve gerar OP na aprovação. False envia gerar_op=false para a API Bremen.")
    modo_agrupamento: str = Field("unidade", description="Modo de agrupamento: 'unidade' (por unidade) ou 'escola' (agrupado por escola)")
