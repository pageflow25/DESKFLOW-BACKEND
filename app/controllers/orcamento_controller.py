import asyncio
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
from ..services.download_bremen_service import DownloadBremenService
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
        
        Fluxo:
        1. Gera orçamentos locais via SQL (agrupa por escola/unidade)
        2. Envia cada orçamento para API Bremen (FASE 01)
        3. Salva na tabela orcamento_api (1 linha por item, com id_distribuicao correto)
        4. Aprova orçamentos na API Bremen (FASE 02)
        5. Salva na tabela aprovacao_api (1 linha por OP, com id_distribuicao sequencial)
        
        Correspondência sequencial:
        - request.itens[i].componentes[0].id_distribuicao → identifica a distribuição
        - response.itens[i] → corresponde ao request.itens[i]
        - OPs da aprovação → correspondem sequencialmente aos itens
        
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
            downloads=0,
            erros=[],
            detalhes=[]
        )
        
        api_service = OrcamentoAPIService()
        
        try:
            # ========================================================
            # FASE 01 — GERAR ORÇAMENTO VIA API
            # ========================================================
            logger.info("=" * 60)
            logger.info("FASE 01 — Gerando orçamentos via API Bremen")
            logger.info("=" * 60)
            
            # Gerar orçamentos locais primeiro (via SQL)
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
            
            if not orcamentos_locais.orcamentos:
                logger.warning("ATENÇÃO: Lista de orçamentos está vazia! Nenhum orçamento será enviado à API.")
                resultado.erros.append("Nenhum orçamento gerado para enviar à API")
                return resultado
            
            # Enviar cada orçamento para a API externa
            for idx, orcamento in enumerate(orcamentos_locais.orcamentos):
                # Delay entre requisições para não sobrecarregar a API Bremen
                if idx > 0:
                    logger.info("Aguardando 3s antes da próxima requisição...")
                    await asyncio.sleep(3)
                
                logger.info(f"Enviando orçamento {idx + 1}/{len(orcamentos_locais.orcamentos)} para API Bremen...")

                try:
                    # Enviar orçamento para API — retorna {resposta_api, payload_enviado}
                    resultado_envio = await api_service.enviar_orcamento(orcamento)
                    resposta_api = resultado_envio["resposta_api"]
                    payload_enviado = resultado_envio["payload_enviado"]
                    resultado.enviados += 1
                    
                    # Extrair dados da resposta
                    id_orcamento = resposta_api.get('data', {}).get('id_orcamento')
                    if not id_orcamento:
                        raise ValueError("ID do orçamento não retornado pela API")
                    
                    # Extrair itens da resposta
                    itens_resposta = api_service.extrair_itens_orcamento(resposta_api)
                    
                    # Extrair lista de id_distribuicao do payload enviado (correspondência sequencial)
                    itens_payload = payload_enviado.get('data', {}).get('itens', [])
                    distribuicoes_ids = []
                    for item_payload in itens_payload:
                        componentes = item_payload.get('componentes', [])
                        if componentes:
                            dist_id = componentes[0].get('id_distribuicao')
                            if dist_id:
                                distribuicoes_ids.append(dist_id)
                    
                    logger.info(
                        f"Orçamento {id_orcamento}: {len(itens_resposta)} itens retornados, "
                        f"{len(distribuicoes_ids)} distribuições mapeadas"
                    )
                    
                    # Salvar na tabela orcamento_api (1 registro por item, com id_distribuicao correto)
                    registros_orcamento = OrcamentoService.salvar_orcamento_api(
                        db=db,
                        id_orcamento=id_orcamento,
                        itens_resposta=itens_resposta,
                        resposta_completa=resposta_api,
                        payload_enviado=payload_enviado
                    )
                    
                    resultado.salvos += 1
                    
                    # Atualizar status de cada distribuição como orcamento_gerado
                    for dist_id in distribuicoes_ids:
                        try:
                            OrcamentoService.atualizar_status_distribuicao(
                                db=db,
                                distribuicao_id=dist_id,
                                novo_status="orcamento_gerado",
                                mensagem=f"Orçamento gerado via API - ID: {id_orcamento}",
                                sucesso=True
                            )
                        except Exception as e:
                            logger.warning(f"Erro ao atualizar status da distribuição {dist_id}: {e}")
                    
                    resultado.detalhes.append({
                        "fase": "01_orcamento",
                        "id_orcamento": id_orcamento,
                        "itens_count": len(itens_resposta),
                        "registros_criados": len(registros_orcamento),
                        "distribuicoes_ids": distribuicoes_ids,
                        "status": "sucesso"
                    })
                    
                    # ========================================================
                    # FASE 02 — APROVAR ORÇAMENTO (se configurado)
                    # ========================================================
                    if request.data_entrega and request.aprovar_automaticamente:
                        logger.info("=" * 60)
                        logger.info(f"FASE 02 — Aprovando orçamento {id_orcamento}")
                        logger.info("=" * 60)
                        
                        # Delay entre fase 1 e fase 2
                        await asyncio.sleep(2)
                        
                        try:
                            # Aprovar orçamento — busca itens da tabela orcamento_api
                            resposta_aprovacao = await api_service.aprovar_orcamento(
                                db=db,
                                id_orcamento=id_orcamento,
                                data_entrega=request.data_entrega
                            )
                            resultado.aprovados += 1
                            
                            # Normalizar resposta
                            if isinstance(resposta_aprovacao, list):
                                logger.warning("Resposta da aprovação veio como lista. Normalizando.")
                                resposta_aprovacao = {"data": resposta_aprovacao}
                            
                            # Salvar na tabela aprovacao_api (1 linha por OP)
                            registros_aprovacao = OrcamentoService.salvar_aprovacao_api(
                                db=db,
                                id_orcamento=id_orcamento,
                                resposta_completa=resposta_aprovacao,
                                distribuicoes_ids=distribuicoes_ids,
                                payload_orcamento=payload_enviado
                            )
                            
                            # Atualizar status de cada distribuição como aprovado
                            for dist_id in distribuicoes_ids:
                                try:
                                    OrcamentoService.atualizar_status_distribuicao(
                                        db=db,
                                        distribuicao_id=dist_id,
                                        novo_status="orcamento_aprovado",
                                        mensagem=f"Orçamento {id_orcamento} aprovado - {len(registros_aprovacao)} OPs geradas",
                                        sucesso=True
                                    )
                                except Exception as e:
                                    logger.warning(f"Erro ao atualizar status da distribuição {dist_id}: {e}")
                            
                            resultado.detalhes.append({
                                "fase": "02_aprovacao",
                                "id_orcamento": id_orcamento,
                                "ops_count": len(registros_aprovacao),
                                "distribuicoes_ids": distribuicoes_ids,
                                "status": "sucesso"
                            })
                            
                        except Exception as e:
                            error_msg = f"Erro na FASE 02 (aprovação) para orçamento {id_orcamento}: {str(e)}"
                            logger.error(error_msg)
                            resultado.erros.append(error_msg)
                            
                            # Atualizar status como erro para cada distribuição
                            for dist_id in distribuicoes_ids:
                                try:
                                    OrcamentoService.atualizar_status_distribuicao(
                                        db=db,
                                        distribuicao_id=dist_id,
                                        novo_status="erro_aprovacao",
                                        mensagem=error_msg,
                                        sucesso=False
                                    )
                                except Exception:
                                    pass
                            
                            resultado.detalhes.append({
                                "fase": "02_aprovacao",
                                "id_orcamento": id_orcamento,
                                "status": "erro",
                                "erro": str(e)
                            })
                    
                    # ========================================================
                    # FASE 03 — BAIXAR E ORGANIZAR ARQUIVOS (se configurado)
                    # ========================================================
                    if request.baixar_arquivos and resultado.aprovados > 0:
                        logger.info("=" * 60)
                        logger.info(f"FASE 03 — Baixando arquivos para orçamento {id_orcamento}")
                        logger.info("=" * 60)
                        
                        # Delay entre fase 2 e fase 3
                        await asyncio.sleep(2)
                        
                        try:
                            download_service = DownloadBremenService()
                            resultado_download = await download_service.processar_downloads_por_orcamento(
                                db=db,
                                id_orcamento=id_orcamento
                            )
                            
                            resultado.downloads += resultado_download.get("downloads", 0)
                            
                            # Atualizar status das distribuições como arquivos_baixados
                            for dist_id in distribuicoes_ids:
                                try:
                                    OrcamentoService.atualizar_status_distribuicao(
                                        db=db,
                                        distribuicao_id=dist_id,
                                        novo_status="arquivos_baixados",
                                        mensagem=f"Arquivos baixados - {resultado_download.get('downloads', 0)} arquivos",
                                        sucesso=True
                                    )
                                except Exception as e:
                                    logger.warning(f"Erro ao atualizar status da distribuição {dist_id}: {e}")
                            
                            resultado.detalhes.append({
                                "fase": "03_download",
                                "id_orcamento": id_orcamento,
                                "arquivos_baixados": resultado_download.get("downloads", 0),
                                "total_ops": resultado_download.get("total_ops", 0),
                                "detalhes_ops": resultado_download.get("detalhes", []),
                                "status": "sucesso"
                            })
                            
                            if resultado_download.get("erros"):
                                for erro_dl in resultado_download["erros"]:
                                    resultado.erros.append(f"FASE 03: {erro_dl}")
                            
                        except Exception as e:
                            error_msg = f"Erro na FASE 03 (download) para orçamento {id_orcamento}: {str(e)}"
                            logger.error(error_msg)
                            resultado.erros.append(error_msg)
                            
                            resultado.detalhes.append({
                                "fase": "03_download",
                                "id_orcamento": id_orcamento,
                                "status": "erro",
                                "erro": str(e)
                            })
                    
                except Exception as e:
                    error_msg = f"Erro na FASE 01 (orçamento) para índice {idx}: {str(e)}"
                    logger.error(error_msg)
                    resultado.erros.append(error_msg)
            
            logger.info("=" * 60)
            logger.info(
                f"Processamento concluído: {resultado.enviados}/{resultado.total} enviados, "
                f"{resultado.aprovados} aprovados, {len(resultado.erros)} erros"
            )
            logger.info("=" * 60)
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro geral no processamento: {str(e)}")
            resultado.erros.append(f"Erro geral: {str(e)}")
            return resultado
