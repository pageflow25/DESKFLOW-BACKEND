from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any
from ..config.logging_config import get_logger
from ..schemas.orcamento import EnviarOrcamentoRequest, ProcessamentoResultado
from ..services.orcamento_service_new import OrcamentoService

logger = get_logger(__name__)


class OrcamentoController:
    """Controller para operações de orçamento"""
    
    @staticmethod
    async def enviar_orcamento_api(
        db: Session, 
        request: EnviarOrcamentoRequest
    ) -> ProcessamentoResultado:
        """
        Novo fluxo: Gera orçamento, envia para API externa e opcionalmente aprova
        
        Args:
            db: Sessão do banco de dados
            request: Dados para envio do orçamento
            
        Returns:
            ProcessamentoResultado com detalhes do processamento
        """
        try:
            logger.info(f"Iniciando processamento de orçamento para escola {request.escola_id}")
            
            # Processar workflow completo usando o novo serviço
            resultado = await OrcamentoService.processar_workflow_completo(db, request)
            
            logger.info(f"Processamento concluído: {resultado.enviados} enviados, {resultado.salvos} salvos, {resultado.aprovados} aprovados")
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro no controller de orçamento: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro interno no processamento do orçamento: {str(e)}"
            )
