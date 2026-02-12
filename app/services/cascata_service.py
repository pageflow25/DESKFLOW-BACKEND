"""
Service para operações de pedidos em cascata
"""

from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List, Optional
from pathlib import Path
from ..config.logging_config import get_logger

logger = get_logger(__name__)

# Diretório do SQL
SQL_DIR = Path(__file__).parent / "sql"


def _carregar_query(nome_arquivo: str) -> str:
    """Carrega uma query SQL de um arquivo"""
    caminho = SQL_DIR / nome_arquivo
    return caminho.read_text(encoding="utf-8")


class CascataService:
    """Service para operações de pedidos em cascata"""
    
    # Cache da query para evitar leitura repetida do disco
    _query_cache: Optional[str] = None

    @classmethod
    def _obter_query(cls) -> str:
        """Obtém a query SQL, usando cache se disponível"""
        if cls._query_cache is None:
            cls._query_cache = _carregar_query("query_cascata.sql")
        return cls._query_cache

    @staticmethod
    def get_pedidos_escola_cascata(
        db: Session, 
        escola_id: int, 
        tipo_formulario: str = 'MEMOREX'
    ) -> List[Dict[str, Any]]:
        """
        Busca detalhes dos pedidos de uma escola em estrutura hierárquica
        
        Args:
            db: Sessão do banco de dados
            escola_id: ID da escola
            tipo_formulario: Tipo de formulário a filtrar (padrão: 'MEMOREX')
            
        Returns:
            Lista de divisões logísticas com produtos, datas e arquivos
        """
        logger.info(
            f"Buscando pedidos em cascata para escola_id={escola_id}, "
            f"tipo_formulario={tipo_formulario}"
        )
        
        return _executar_query_cascata(db, escola_id, tipo_formulario)


def _executar_query_cascata(
    db: Session, 
    escola_id: int, 
    tipo_formulario: str
) -> List[Dict[str, Any]]:
    """Executa a query de cascata e retorna os resultados"""
    try:
        query_sql = CascataService._obter_query()
        query = text(query_sql)
        
        result = db.execute(query, {
            "escola_id": escola_id, 
            "tipo_formulario": tipo_formulario
        })
        row = result.fetchone()
        
        if row and row.dashboard_completo:
            logger.info(f"Dados em cascata obtidos com sucesso para escola_id={escola_id}")
            return row.dashboard_completo
        
        logger.warning(f"Nenhum dado encontrado para escola_id={escola_id}")
        return []
        
    except Exception as e:
        logger.error(
            f"Erro ao buscar pedidos em cascata para escola_id={escola_id}: {str(e)}", 
            exc_info=True
        )
        raise
