"""
Service para operações de pedidos em cascata
"""

from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam, Integer
from sqlalchemy.dialects.postgresql import ARRAY
from typing import Dict, Any, List, Optional
from pathlib import Path
from ..config.logging_config import get_logger
from ..models.status_deskflow_pedido import StatusDeskflowPedido

logger = get_logger(__name__)

# Diretório do SQL
SQL_DIR = Path(__file__).parent / "sql"


def _carregar_query(nome_arquivo: str) -> str:
    """Carrega uma query SQL de um arquivo"""
    caminho = SQL_DIR / nome_arquivo
    return caminho.read_text(encoding="utf-8-sig")


class CascataService:
    """Service para operações de pedidos em cascata"""

    @staticmethod
    def get_pedidos_escola_cascata(
        db: Session, 
        escola_id: int, 
        tipo_formulario: str = None,
        ids_formularios: list = None,
        status_ids: list = None
    ) -> List[Dict[str, Any]]:
        """
        Busca detalhes dos pedidos de uma escola em estrutura hierárquica
        
        Args:
            db: Sessão do banco de dados
            escola_id: ID da escola
            tipo_formulario: Tipo de formulário a filtrar (opcional)
            ids_formularios: Lista de IDs de formulários para filtrar (opcional)
            status_ids: Lista de IDs de status para filtrar (padrão: [1])
            
        Returns:
            Lista de divisões logísticas com produtos, datas e arquivos
        """
        logger.info(
            f"Buscando pedidos em cascata para escola_id={escola_id}, "
            f"tipo_formulario={tipo_formulario}, ids_formularios={ids_formularios}, status_ids={status_ids}"
        )
        
        return _executar_query_cascata(db, escola_id, tipo_formulario, ids_formularios, status_ids)

    @staticmethod
    def listar_status_deskflow(db: Session) -> List[Dict[str, Any]]:
        """Retorna opções de status da tabela status_deskflow_pedido para filtros de UI."""
        rows = (
            db.query(
                StatusDeskflowPedido.id,
                StatusDeskflowPedido.codigo,
                StatusDeskflowPedido.descricao,
            )
            .order_by(StatusDeskflowPedido.id.asc())
            .all()
        )

        return [
            {
                "id": row.id,
                "codigo": row.codigo,
                "descricao": row.descricao,
            }
            for row in rows
        ]


def _executar_query_cascata(
    db: Session, 
    escola_id: int, 
    tipo_formulario: str,
    ids_formularios: list = None,
    status_ids: list = None
) -> List[Dict[str, Any]]:
    """Executa a query de cascata e retorna os resultados"""
    try:
        query_sql = _carregar_query("query_cascata.sql")
        query = text(query_sql).bindparams(
            bindparam("ids_formularios", type_=ARRAY(Integer)),
            bindparam("status_ids", type_=ARRAY(Integer)),
        )
        
        result = db.execute(query, {
            "escola_id": escola_id, 
            "tipo_formulario": tipo_formulario,
            "ids_formularios": ids_formularios,
            "status_ids": status_ids
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
