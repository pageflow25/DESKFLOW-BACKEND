from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any
from ..config.logging_config import get_logger
from ..schemas.pedido_cascata import PedidoCascataResponse, DivisaoLogisticaInfo
from ..services.cascata_service import CascataService

logger = get_logger(__name__)


class CascataController:
    """Controller para operações de pedidos em cascata"""
    
    @staticmethod
    async def get_pedidos_escola(db: Session, escola_id: int, tipo_formulario: str = 'MEMOREX') -> PedidoCascataResponse:
        """
        Obtém pedidos da escola em estrutura hierárquica
        
        Args:
            db: Sessão do banco de dados
            escola_id: ID da escola
            tipo_formulario: Tipo de formulário a filtrar (padrão: 'MEMOREX')
            
        Returns:
            PedidoCascataResponse com dados em cascata
        """
        logger.info(f"Requisição para obter pedidos em cascata da escola {escola_id}, tipo_formulario={tipo_formulario}")
        
        if escola_id <= 0:
            logger.warning(f"ID de escola inválido: {escola_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ID da escola deve ser maior que zero"
            )
        
        try:
            dashboard_data = CascataService.get_pedidos_escola_cascata(db, escola_id, tipo_formulario)
            
            response = PedidoCascataResponse(
                dashboard_completo=dashboard_data
            )
            
            logger.info(f"Pedidos em cascata obtidos para escola {escola_id}")
            return response
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro no controller de cascata: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro ao buscar pedidos da escola: {str(e)}"
            )
