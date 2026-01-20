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
        logger.info(f"Requisição para gerar orçamento: escola={request.escola_id}, produtos={request.ids_produtos}")
        
        # Validações
        if request.escola_id <= 0:
            logger.warning(f"ID de escola inválido: {request.escola_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ID da escola deve ser maior que zero"
            )
        
        if not request.ids_produtos:
            logger.warning("Lista de produtos vazia")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Deve ser fornecido ao menos um produto"
            )
        
        if not request.datas_saida:
            logger.warning("Lista de datas vazia")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Deve ser fornecida ao menos uma data de saída"
            )
        
        try:
            orcamentos = OrcamentoService.gerar_orcamento(
                db=db,
                escola_id=request.escola_id,
                ids_produtos=request.ids_produtos,
                datas_saida=request.datas_saida,
                divisoes_logistica=request.divisoes_logistica,
                dias_uteis_filtro=request.dias_uteis_filtro
            )
            
            # Salvar orçamento em arquivo
            nome_arquivo = None
            try:
                nome_arquivo = ArquivoOrcamentoService.salvar_orcamento(
                    orcamentos=orcamentos,
                    escola_id=request.escola_id,
                    ids_produtos=request.ids_produtos
                )
                logger.info(f"Orçamento salvo em arquivo: {nome_arquivo}")
            except Exception as e:
                logger.warning(f"Erro ao salvar arquivo de orçamento: {str(e)}")
            
            response = OrcamentoListResponse(
                orcamentos=orcamentos,
                total_unidades=len(orcamentos),
                arquivo=nome_arquivo,
                mensagem=f"Orçamento gerado com sucesso para {len(orcamentos)} unidade(s)"
            )
            
            logger.info(f"Orçamento gerado com sucesso: {len(orcamentos)} unidades")
            return response
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro no controller de orçamento: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro ao gerar orçamento: {str(e)}"
            )

    @staticmethod
    async def enviar_orcamento_api(db: Session, request: EnviarOrcamentoRequest) -> ProcessamentoResultado:
        """
        Gera orçamento, envia para API externa e salva no banco
        
        Args:
            db: Sessão do banco de dados
            request: Dados para geração e envio do orçamento
            
        Returns:
            ProcessamentoResultado com resumo do processamento
        """
        logger.info(f"Requisição para enviar orçamento à API: escola={request.escola_id}")
        
        # Validações
        if request.escola_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ID da escola deve ser maior que zero"
            )
        
        if not request.ids_produtos:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Deve ser fornecido ao menos um produto"
            )
        
        if not request.datas_saida:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Deve ser fornecida ao menos uma data de saída"
            )
        
        try:
            # 1. Gerar orçamentos
            orcamentos = OrcamentoService.gerar_orcamento(
                db=db,
                escola_id=request.escola_id,
                ids_produtos=request.ids_produtos,
                datas_saida=request.datas_saida,
                divisoes_logistica=request.divisoes_logistica,
                dias_uteis_filtro=request.dias_uteis_filtro
            )
            
            if not orcamentos:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Nenhum orçamento gerado com os filtros fornecidos"
                )
            
            # 2. Processar: enviar para API e salvar no banco
            resultado = OrcamentoAPIService.processar_orcamentos(
                db=db,
                orcamentos=orcamentos,
                aprovar_automaticamente=request.aprovar_automaticamente,
                data_entrega=request.data_entrega
            )
            
            logger.info(f"Processamento concluído: {resultado['enviados']}/{resultado['total']} enviados")
            return ProcessamentoResultado(**resultado)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro ao enviar orçamento para API: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro ao enviar orçamento para API: {str(e)}"
            )

