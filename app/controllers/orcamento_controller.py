from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any, List
from ..config.logging_config import get_logger
from ..schemas.orcamento import (
    OrcamentoRequest, 
    OrcamentoListResponse, 
    FluxoOrcamentoRequest, 
    ProcessamentoResultado
)
from ..services.orcamento_service import OrcamentoService
from ..services.orcamento_api_service import OrcamentoAPIService
from datetime import datetime


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
        try:
            logger.info(f"Iniciando geração de orçamento para escola {request.escola_id}")
            
            # Gerar orçamento usando o service
            resultado = OrcamentoService.gerar_orcamento(db, request)
            
            logger.info(f"Orçamento gerado com sucesso: {resultado.total_unidades} unidades")
            return resultado
            
        except Exception as e:
            logger.error(f"Erro no controller ao gerar orçamento: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro interno ao gerar orçamento: {str(e)}"
            )
    
    @staticmethod
    async def processar_orcamento_com_distribuicao(
        db: Session, 
        request: FluxoOrcamentoRequest
    ) -> ProcessamentoResultado:
        """
        Processa orçamento completo com distribuição (FASE 01 + FASE 02)
        
        Args:
            db: Sessão do banco de dados
            request: Dados para processamento
            
        Returns:
            ProcessamentoResultado com detalhes do processamento
        """
        logger.info(f"Iniciando processamento completo para escola {request.escola_id}")
        
        resultado = ProcessamentoResultado(
            total=0,
            enviados=0,
            aprovados=0,
            salvos=0,
            erros=[],
            detalhes=[]
        )
        
        api_service = OrcamentoAPIService()
        
        try:
            # FASE 01 - GERAR ORÇAMENTO VIA API
            logger.info("FASE 01 - Gerando orçamento via API")
            
            # Gerar orçamentos locais primeiro
            orcamento_request = OrcamentoRequest(
                escola_id=request.escola_id,
                ids_produtos=request.ids_produtos,
                datas_saida=request.datas_saida,
                divisoes_logistica=request.divisoes_logistica,
                dias_uteis_filtro=request.dias_uteis_filtro
            )
            
            orcamentos_locais = await OrcamentoController.gerar_orcamento(db, orcamento_request)
            resultado.total = orcamentos_locais.total_unidades
            
            logger.info(f"Orçamentos gerados localmente: {resultado.total} unidade(s)")
            
            # Obter distribuições relevantes
            distribuicoes = OrcamentoService.obter_distribuicoes_por_escola(
                db, request.escola_id, request.ids_produtos
            )
            
            logger.info(f"Distribuições encontradas: {len(distribuicoes)}")
            logger.info(f"Orçamentos a enviar para API: {len(orcamentos_locais.orcamentos)}")
            
            if not orcamentos_locais.orcamentos:
                logger.warning("ATENÇÃO: Lista de orçamentos está vazia! Nenhum orçamento será enviado à API.")
                resultado.erros.append("Nenhum orçamento gerado para enviar à API")
                return resultado
            
            # Enviar cada orçamento para a API externa
            for idx, orcamento in enumerate(orcamentos_locais.orcamentos):
                logger.info(f"Enviando orçamento {idx + 1}/{len(orcamentos_locais.orcamentos)} para API Bremen...")

                try:
                    # Enviar orçamento para API
                    resposta_api = await api_service.enviar_orcamento(orcamento)
                    resultado.enviados += 1
                    
                    # Extrair dados da resposta
                    id_orcamento = resposta_api.get('data', {}).get('id_orcamento')
                    if not id_orcamento:
                        raise ValueError("ID do orçamento não retornado pela API")
                    
                    # Extrair itens
                    itens = api_service.extrair_itens_orcamento(resposta_api)
                    
                    # Salvar na tabela orcamento_api (um registro por item)
                    if idx < len(distribuicoes):
                        distribuicao = distribuicoes[idx]
                        
                        # Retorna lista de registros salvos (um por item)
                        registros_orcamento = OrcamentoService.salvar_orcamento_api(
                            db=db,
                            distribuicao_id=distribuicao.id,
                            id_orcamento=id_orcamento,
                            itens=itens,
                            resposta_completa=resposta_api
                        )
                        
                        # Atualizar status
                        OrcamentoService.atualizar_status_distribuicao(
                            db=db,
                            distribuicao_id=distribuicao.id,
                            novo_status="orcamento_gerado",
                            mensagem=f"Orçamento gerado via API - ID: {id_orcamento}",
                            sucesso=True
                        )
                        
                        resultado.salvos += 1
                        
                        resultado.detalhes.append({
                            "fase": "01_orcamento",
                            "distribuicao_id": distribuicao.id,
                            "id_orcamento": id_orcamento,
                            "itens_count": len(itens),
                            "registros_criados": len(registros_orcamento),
                            "status": "sucesso"
                        })
                        
                        # FASE 02 - APROVAR ORÇAMENTO (se data_entrega fornecida)
                        if request.data_entrega and request.aprovar_automaticamente:
                            logger.info(f"FASE 02 - Aprovando orçamento {id_orcamento}")
                            
                            try:
                                # Aprovar orçamento - busca itens da tabela orcamento_api
                                resposta_aprovacao = await api_service.aprovar_orcamento(
                                    db=db,
                                    id_orcamento=id_orcamento,
                                    data_entrega=request.data_entrega
                                )
                                resultado.aprovados += 1
                                
                                # Extrair dados da aprovação
                                id_ops = resposta_aprovacao.get('data', {}).get('id_ops')
                                pedidos = api_service.extrair_pedidos_aprovacao(resposta_aprovacao)
                                
                                # Salvar na tabela aprovacao_api
                                aprovacao_api = OrcamentoService.salvar_aprovacao_api(
                                    db=db,
                                    distribuicao_id=distribuicao.id,
                                    id_orcamento=id_orcamento,
                                    id_ops=id_ops,
                                    pedidos=pedidos,
                                    resposta_completa=resposta_aprovacao
                                )
                                
                                # Atualizar status
                                OrcamentoService.atualizar_status_distribuicao(
                                    db=db,
                                    distribuicao_id=distribuicao.id,
                                    novo_status="orcamento_aprovado",
                                    mensagem=f"Orçamento aprovado - OPs: {id_ops}",
                                    sucesso=True
                                )
                                
                                resultado.detalhes.append({
                                    "fase": "02_aprovacao",
                                    "distribuicao_id": distribuicao.id,
                                    "id_orcamento": id_orcamento,
                                    "id_ops": id_ops,
                                    "pedidos_count": len(pedidos),
                                    "status": "sucesso"
                                })
                                
                            except Exception as e:
                                error_msg = f"Erro na FASE 02 (aprovação) para distribuição {distribuicao.id}: {str(e)}"
                                logger.error(error_msg)
                                resultado.erros.append(error_msg)
                                
                                # Atualizar status como erro
                                OrcamentoService.atualizar_status_distribuicao(
                                    db=db,
                                    distribuicao_id=distribuicao.id,
                                    novo_status="erro_aprovacao",
                                    mensagem=error_msg,
                                    sucesso=False
                                )
                                
                                resultado.detalhes.append({
                                    "fase": "02_aprovacao",
                                    "distribuicao_id": distribuicao.id,
                                    "id_orcamento": id_orcamento,
                                    "status": "erro",
                                    "erro": str(e)
                                })
                    
                except Exception as e:
                    error_msg = f"Erro na FASE 01 (orçamento) para índice {idx}: {str(e)}"
                    logger.error(error_msg)
                    resultado.erros.append(error_msg)
                    
                    if idx < len(distribuicoes):
                        distribuicao = distribuicoes[idx]
                        OrcamentoService.atualizar_status_distribuicao(
                            db=db,
                            distribuicao_id=distribuicao.id,
                            novo_status="erro_orcamento",
                            mensagem=error_msg,
                            sucesso=False
                        )
            
            logger.info(f"Processamento concluído: {resultado.enviados}/{resultado.total} enviados, "
                       f"{resultado.aprovados} aprovados, {len(resultado.erros)} erros")
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro geral no processamento: {str(e)}")
            resultado.erros.append(f"Erro geral: {str(e)}")
            return resultado
