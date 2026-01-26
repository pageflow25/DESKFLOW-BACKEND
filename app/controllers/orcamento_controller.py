from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any
from ..config.logging_config import get_logger
from ..schemas.orcamento import OrcamentoRequest, OrcamentoListResponse, EnviarOrcamentoRequest, ProcessamentoResultado
from ..services.orcamento_service import OrcamentoService
from ..services.orcamento_api_service import OrcamentoAPIService
from ..services.arquivo_orcamento_service import ArquivoOrcamentoService

logger = get_logger(__name__)


class OrcamentoController:
    """Controller para operações de orçamento"""
    
    @staticmethod
    async def gerar_orcamento(db: Session, request: OrcamentoRequest) -> OrcamentoListResponse:
        """
        Gera orçamento com base nos filtros fornecidos
        
        Args:
            db: Sessão do banco de dados
            request: Dados para geração do orçamento
            
        Returns:
            OrcamentoListResponse com orçamentos gerados
        """
