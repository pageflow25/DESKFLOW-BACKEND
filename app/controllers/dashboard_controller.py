from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any
from ..config.logging_config import get_logger
from ..schemas.dashboard import DashboardResponse, EscolaListItem
from ..services.dashboard_service import DashboardService

logger = get_logger(__name__)


class DashboardController:
    """Controller para operações do dashboard"""
    
    @staticmethod
    async def get_escolas(db: Session) -> DashboardResponse:
        """
        Obtém lista de escolas com contagem de pedidos
        
        Args:
            db: Sessão do banco de dados
            
        Returns:
            DashboardResponse com lista de escolas
        """
        logger.info("Requisição para obter escolas do dashboard")
        
        try:
            escolas_data = DashboardService.get_escolas_com_pedidos(db)
            
            escolas_list = [
                EscolaListItem(**escola) for escola in escolas_data
            ]
            
            response = DashboardResponse(
                escolas=escolas_list,
                total_escolas=len(escolas_list)
            )
            
            logger.info(f"Dashboard retornou {len(escolas_list)} escolas")
            return response
            
        except Exception as e:
            logger.error(f"Erro no controller do dashboard: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro ao buscar dados do dashboard: {str(e)}"
            )
