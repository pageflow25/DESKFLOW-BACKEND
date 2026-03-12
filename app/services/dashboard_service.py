from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict, Any
from ..config.logging_config import get_logger

logger = get_logger(__name__)


class DashboardService:
    """Service para operações do dashboard"""

    @staticmethod
    def get_escolas_com_pedidos(db: Session) -> List[Dict[str, Any]]:
        """
        Busca todas as escolas agrupadas com contagem de pedidos
        
        Args:
            db: Sessão do banco de dados
            
        Returns:
            Lista de escolas com seus dados e total de pedidos
        """
        logger.info("Buscando escolas (incluindo sem pedidos) com contagem/alerta")
        
        query = text("""
            SELECT
                e.id AS escola_id,
                e.nome AS nome_escola,
                e.codigo AS codigo_escola,
                COUNT(DISTINCT f.id) AS total_pedidos,
                CASE WHEN COUNT(DISTINCT f.id) > 0 THEN TRUE ELSE FALSE END AS possui_pedidos_alerta
            FROM escolas e
            LEFT JOIN unidades_escolares ue
                ON ue.escola_id = e.id
            LEFT JOIN distribuicao_materiais dm
                ON dm.unidade_escolar_id = ue.id
               AND dm.status_id = 1
            LEFT JOIN formularios f
                ON f.id = dm.formulario_id
            GROUP BY
                e.id,
                e.nome,
                e.codigo
            ORDER BY e.nome;
        """)
        
        try:
            result = db.execute(query)
            escolas = []
            
            for row in result:
                escola_dict = {
                    "escola_id": row.escola_id,
                    "nome_escola": row.nome_escola,
                    "codigo_escola": row.codigo_escola,
                    "total_pedidos": int(row.total_pedidos or 0),
                    "possui_pedidos_alerta": bool(row.possui_pedidos_alerta)
                }
                escolas.append(escola_dict)
            
            logger.info(f"Encontradas {len(escolas)} escolas com pedidos")
            return escolas
            
        except Exception as e:
            logger.error(f"Erro ao buscar escolas com pedidos: {str(e)}", exc_info=True)
            raise
