"""
Service para geração de orçamentos
"""

from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List, Optional
from datetime import date
from pathlib import Path
from ..config.logging_config import get_logger

logger = get_logger(__name__)

# Diretório do SQL
SQL_DIR = Path(__file__).parent / "sql"


def _carregar_query(nome_arquivo: str) -> str:
    """Carrega uma query SQL de um arquivo"""
    caminho = SQL_DIR / nome_arquivo
    return caminho.read_text(encoding="utf-8")


def _formatar_array_int(valores: List[int]) -> str:
    """Formata uma lista de inteiros para array PostgreSQL"""
    return '{' + ','.join(map(str, valores)) + '}'


def _formatar_array_date(datas: List[date]) -> str:
    """Formata uma lista de datas para array PostgreSQL"""
    def formatar_data(d):
        return d.strftime('%Y-%m-%d') if isinstance(d, date) else str(d)
    return '{' + ','.join(formatar_data(d) for d in datas) + '}'


def _formatar_array_text(valores: Optional[List[str]]) -> Optional[str]:
    """Formata uma lista de strings para array PostgreSQL"""
    if not valores:
        return None
    return '{' + ','.join(f'"{v}"' for v in valores) + '}'


def _formatar_array_int_opcional(valores: Optional[List[int]]) -> Optional[str]:
    """Formata uma lista opcional de inteiros para array PostgreSQL"""
    if not valores:
        return None
    return '{' + ','.join(map(str, valores)) + '}'


class OrcamentoService:
    """Service para geração de orçamentos"""
    
    # Cache da query para evitar leitura repetida do disco
    _query_cache: Optional[str] = None

    @classmethod
    def _obter_query(cls) -> str:
        """Obtém a query SQL, usando cache se disponível"""
        if cls._query_cache is None:
            cls._query_cache = _carregar_query("query_orcamento.sql")
        return cls._query_cache

    @staticmethod
    def gerar_orcamento(
        db: Session, 
        escola_id: int, 
        ids_produtos: List[int], 
        datas_saida: List[date],
        divisoes_logistica: Optional[List[str]] = None,
        dias_uteis_filtro: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Gera orçamentos separados por unidade escolar
        
        Args:
            db: Sessão do banco de dados
            escola_id: ID da escola
            ids_produtos: Lista de IDs de produtos selecionados
            datas_saida: Lista de datas de saída selecionadas
            divisoes_logistica: Lista de divisões logísticas (opcional)
            dias_uteis_filtro: Lista de dias úteis (opcional)
            
        Returns:
            Lista de orçamentos (um por unidade)
        """
        logger.info(
            f"Gerando orçamento para escola_id={escola_id}, "
            f"produtos={ids_produtos}, datas={datas_saida}, "
            f"divisoes={divisoes_logistica}, dias_uteis={dias_uteis_filtro}"
        )
        
        parametros = _preparar_parametros(
            escola_id=escola_id,
            ids_produtos=ids_produtos,
            datas_saida=datas_saida,
            divisoes_logistica=divisoes_logistica,
            dias_uteis_filtro=dias_uteis_filtro
        )
        
        return _executar_query_orcamento(db, parametros)


def _preparar_parametros(
    escola_id: int,
    ids_produtos: List[int],
    datas_saida: List[date],
    divisoes_logistica: Optional[List[str]],
    dias_uteis_filtro: Optional[List[int]]
) -> Dict[str, Any]:
    """Prepara os parâmetros para a query de orçamento"""
    return {
        "escola_id": escola_id,
        "ids_produtos": _formatar_array_int(ids_produtos),
        "datas_saida": _formatar_array_date(datas_saida),
        "divisoes_logistica": _formatar_array_text(divisoes_logistica),
        "dias_uteis_filtro": _formatar_array_int_opcional(dias_uteis_filtro)
    }


def _executar_query_orcamento(db: Session, parametros: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Executa a query de orçamento e retorna os resultados"""
    try:
        query_sql = OrcamentoService._obter_query()
        query = text(query_sql)
        
        result = db.execute(query, parametros)
        orcamentos = [row.orcamento for row in result if row.orcamento]
        
        logger.info(f"Gerados {len(orcamentos)} orçamentos para escola_id={parametros['escola_id']}")
        return orcamentos
        
    except Exception as e:
        logger.error(f"Erro ao gerar orçamento: {str(e)}", exc_info=True)
        raise
