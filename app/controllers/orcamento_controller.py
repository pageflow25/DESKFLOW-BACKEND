import asyncio
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List
from ..config.logging_config import get_logger
from ..schemas.orcamento import (
    OrcamentoRequest, 
    OrcamentoListResponse, 
    OrcamentoResponse,
    FluxoOrcamentoRequest, 
    ProcessamentoResultado
)
from ..services.orcamento_service import OrcamentoService
from ..services.orcamento_api_service import OrcamentoAPIService
from ..services.download_bremen_service import DownloadBremenService
from datetime import datetime


logger = get_logger(__name__)

# Delays entre requisições (segundos)
DELAY_ENTRE_ORCAMENTOS = 3
DELAY_ENTRE_FASES = 2


class OrcamentoController:
    """Controller para operações de orçamento"""
    
    # ================================================================
    # GERAÇÃO LOCAL
    # ================================================================

    @staticmethod
    async def gerar_orcamento(db: Session, request: OrcamentoRequest) -> OrcamentoListResponse:
        """
        Gera orçamento com base nos filtros fornecidos (via SQL local).
        
        Args:
            db: Sessão do banco de dados
            request: Dados para geração do orçamento
            
        Returns:
            OrcamentoListResponse com orçamentos gerados
        """
        try:
            logger.info(f"Iniciando geração de orçamento para escola {request.escola_id}")
            resultado = OrcamentoService.gerar_orcamento(db, request)
            logger.info(f"Orçamento gerado com sucesso: {resultado.total_unidades} unidades")
            return resultado
        except Exception as e:
            logger.error(f"Erro no controller ao gerar orçamento: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro interno ao gerar orçamento: {str(e)}"
            )

    # ================================================================
    # HELPERS
    # ================================================================

    @staticmethod
    def _extrair_distribuicoes_ids(
        payload_enviado: Dict[str, Any], 
        modo: str
    ) -> List[int]:
        """
        Extrai IDs de distribuição do payload enviado (correspondência sequencial).
        
        - Modo unidade: id_distribuicao está em componentes[0]
        - Modo escola: ids_distribuicao é um array no item
        
        Args:
            payload_enviado: Payload original enviado para a API
            modo: 'unidade' ou 'escola'
            
        Returns:
            Lista de IDs de distribuição
        """
        itens_payload = payload_enviado.get('data', {}).get('itens', [])
        distribuicoes_ids = []

        for item_payload in itens_payload:
            if modo == 'escola':
                ids_dist = item_payload.get('ids_distribuicao')
                if ids_dist and isinstance(ids_dist, list):
                    distribuicoes_ids.extend(ids_dist)
            else:
                componentes = item_payload.get('componentes', [])
                if componentes:
                    dist_id = componentes[0].get('id_distribuicao')
                    if dist_id:
                        distribuicoes_ids.append(dist_id)

        return distribuicoes_ids

    @staticmethod
    def _atualizar_status_distribuicoes(
        db: Session, 
        distribuicoes_ids: List[int], 
        novo_status: str, 
        mensagem: str, 
        sucesso: bool = True,
        grupo_lote_id: int = None
    ):
        """
        Atualiza o status de múltiplas distribuições em uma única operação de banco.
        Inclui lógica de cascata capa/miolo via atualizar_status_em_lote.
        """
        if not distribuicoes_ids:
            return
        try:
            OrcamentoService.atualizar_status_em_lote(
                db=db,
                distribuicoes_ids=distribuicoes_ids,
                novo_status=novo_status,
                mensagem=mensagem,
                sucesso=sucesso,
                grupo_lote_id=grupo_lote_id,
            )
        except Exception as e:
            logger.warning(f"Erro ao atualizar status em lote: {e}")

    # ================================================================
    # FASE 01 — GERAR ORÇAMENTO VIA API BREMEN
    # ================================================================

    @staticmethod
    async def _fase01_enviar_orcamento(
        db: Session,
        api_service: OrcamentoAPIService,
        orcamento: OrcamentoResponse,
        modo: str,
        grupo_lote_id: int = None
    ) -> Dict[str, Any]:
        """
        FASE 01 — Envia um orçamento para a API Bremen e salva na tabela orcamento_api.
        
        Args:
            db: Sessão do banco de dados
            api_service: Instância do OrcamentoAPIService
            orcamento: Dados do orçamento para envio
            modo: Modo de agrupamento ('unidade' ou 'escola')
            
        Returns:
            Dict com id_orcamento, distribuicoes_ids, payload_enviado e detalhes
            
        Raises:
            Exception: Se o envio falhar
        """
        # Enviar orçamento para API — retorna {resposta_api, payload_enviado}
        resultado_envio = await api_service.enviar_orcamento(orcamento)
        resposta_api = resultado_envio["resposta_api"]
        payload_enviado = resultado_envio["payload_enviado"]

        # Extrair ID do orçamento
        id_orcamento = resposta_api.get('data', {}).get('id_orcamento')
        if not id_orcamento:
            raise ValueError("ID do orçamento não retornado pela API")

        # Extrair itens e distribuições
        itens_resposta = api_service.extrair_itens_orcamento(resposta_api)
        distribuicoes_ids = OrcamentoController._extrair_distribuicoes_ids(payload_enviado, modo)

        logger.info(
            f"Orçamento {id_orcamento}: {len(itens_resposta)} itens retornados, "
            f"{len(distribuicoes_ids)} distribuições mapeadas"
        )

        # Salvar na tabela orcamento_api (1 registro por item)
        registros_orcamento = OrcamentoService.salvar_orcamento_api(
            db=db,
            id_orcamento=id_orcamento,
            itens_resposta=itens_resposta,
            resposta_completa=resposta_api,
            payload_enviado=payload_enviado
        )

        # Atualizar status das distribuições
        OrcamentoController._atualizar_status_distribuicoes(
            db, distribuicoes_ids,
            novo_status="orcamento_gerado",
            mensagem=f"Orçamento gerado via API - ID: {id_orcamento}",
            grupo_lote_id=grupo_lote_id
        )

        return {
            "id_orcamento": id_orcamento,
            "distribuicoes_ids": distribuicoes_ids,
            "payload_enviado": payload_enviado,
            "detalhe": {
                "fase": "01_orcamento",
                "id_orcamento": id_orcamento,
                "itens_count": len(itens_resposta),
                "registros_criados": len(registros_orcamento),
                "distribuicoes_ids": distribuicoes_ids,
                "status": "sucesso"
            }
        }

    # ================================================================
    # FASE 02 — APROVAR ORÇAMENTO NA API BREMEN
    # ================================================================

    @staticmethod
    async def _fase02_aprovar_orcamento(
        db: Session,
        api_service: OrcamentoAPIService,
        id_orcamento: int,
        data_entrega: str,
        distribuicoes_ids: List[int],
        payload_enviado: Dict[str, Any],
        gerar_op: bool = True,
        grupo_lote_id: int = None
    ) -> Dict[str, Any]:
        """
        FASE 02 — Aprova um orçamento na API Bremen e salva na tabela aprovacao_api.
        
        Args:
            db: Sessão do banco de dados
            api_service: Instância do OrcamentoAPIService
            id_orcamento: ID do orçamento a aprovar
            data_entrega: Data de entrega (ISO format)
            distribuicoes_ids: Lista de IDs de distribuição
            payload_enviado: Payload original do orçamento
            
        Returns:
            Dict com detalhes da aprovação
        """
        logger.info("=" * 60)
        logger.info(f"FASE 02 — Aprovando orçamento {id_orcamento}")
        logger.info("=" * 60)

        await asyncio.sleep(DELAY_ENTRE_FASES)

        try:
            resposta_aprovacao = await api_service.aprovar_orcamento(
                db=db,
                id_orcamento=id_orcamento,
                data_entrega=data_entrega,
                gerar_op=gerar_op
            )

            # Normalizar resposta (pode vir como lista)
            if isinstance(resposta_aprovacao, list):
                logger.warning("Resposta da aprovação veio como lista. Normalizando.")
                resposta_aprovacao = {"data": resposta_aprovacao}

            # Salvar na tabela aprovacao_api (1 linha por OP, ou 1 por distribuição se sem OP)
            registros_aprovacao = OrcamentoService.salvar_aprovacao_api(
                db=db,
                id_orcamento=id_orcamento,
                resposta_completa=resposta_aprovacao,
                distribuicoes_ids=distribuicoes_ids,
                payload_orcamento=payload_enviado
            )

            # Verificar se OPs foram geradas (id_ops=None significa apenas pedido de venda)
            tem_op = any(r.id_ops is not None for r in registros_aprovacao)

            if tem_op:
                status_aprovacao = "orcamento_aprovado"
                msg_status = f"Orçamento {id_orcamento} aprovado - {len(registros_aprovacao)} OPs geradas"
            else:
                status_aprovacao = "pedido_venda_gerado"
                msg_status = f"Orçamento {id_orcamento} aprovado como pedido de venda (sem OP)"
                logger.info(f"Orçamento {id_orcamento}: sem OP gerada, FASE 03 será ignorada.")

            # Atualizar status das distribuições
            OrcamentoController._atualizar_status_distribuicoes(
                db, distribuicoes_ids,
                novo_status=status_aprovacao,
                mensagem=msg_status,
                grupo_lote_id=grupo_lote_id
            )

            return {
                "aprovado": True,
                "tem_op": tem_op,
                "detalhe": {
                    "fase": "02_aprovacao",
                    "id_orcamento": id_orcamento,
                    "ops_count": len([r for r in registros_aprovacao if r.id_ops is not None]),
                    "distribuicoes_ids": distribuicoes_ids,
                    "gerar_op": gerar_op,
                    "status": "sucesso"
                }
            }

        except Exception as e:
            error_msg = f"Erro na FASE 02 (aprovação) para orçamento {id_orcamento}: {str(e)}"
            logger.error(error_msg)

            # Marcar distribuições com erro
            OrcamentoController._atualizar_status_distribuicoes(
                db, distribuicoes_ids,
                novo_status="erro_aprovacao",
                mensagem=error_msg,
                sucesso=False,
                grupo_lote_id=grupo_lote_id
            )

            return {
                "aprovado": False,
                "erro": error_msg,
                "detalhe": {
                    "fase": "02_aprovacao",
                    "id_orcamento": id_orcamento,
                    "status": "erro",
                    "erro": str(e)
                }
            }

    # ================================================================
    # FASE 03 — BAIXAR E ORGANIZAR ARQUIVOS
    # ================================================================

    @staticmethod
    async def _fase03_baixar_arquivos(
        db: Session,
        id_orcamento: int,
        distribuicoes_ids: List[int],
        grupo_lote_id: int = None
    ) -> Dict[str, Any]:
        """
        FASE 03 — Baixa e organiza os arquivos PDF de um orçamento aprovado.
        
        Args:
            db: Sessão do banco de dados
            id_orcamento: ID do orçamento aprovado
            distribuicoes_ids: Lista de IDs de distribuição
            
        Returns:
            Dict com detalhes dos downloads
        """
        logger.info("=" * 60)
        logger.info(f"FASE 03 — Baixando arquivos para orçamento {id_orcamento}")
        logger.info("=" * 60)

        await asyncio.sleep(DELAY_ENTRE_FASES)

        try:
            download_service = DownloadBremenService()
            resultado_download = await download_service.processar_downloads_por_orcamento(
                db=db,
                id_orcamento=id_orcamento
            )

            total_downloads = resultado_download.get("downloads", 0)

            # Atualizar status das distribuições
            OrcamentoController._atualizar_status_distribuicoes(
                db, distribuicoes_ids,
                novo_status="arquivos_baixados",
                mensagem=f"Arquivos baixados - {total_downloads} arquivos",
                grupo_lote_id=grupo_lote_id
            )

            detalhe = {
                "fase": "03_download",
                "id_orcamento": id_orcamento,
                "arquivos_baixados": total_downloads,
                "total_ops": resultado_download.get("total_ops", 0),
                "detalhes_ops": resultado_download.get("detalhes", []),
                "status": "sucesso"
            }

            erros_download = []
            if resultado_download.get("erros"):
                erros_download = [f"FASE 03: {e}" for e in resultado_download["erros"]]

            return {
                "downloads": total_downloads,
                "erros": erros_download,
                "detalhe": detalhe
            }

        except Exception as e:
            error_msg = f"Erro na FASE 03 (download) para orçamento {id_orcamento}: {str(e)}"
            logger.error(error_msg)
            try:
                db.rollback()
                logger.debug("Rollback realizado após erro na FASE 03")
            except Exception:
                pass

            return {
                "downloads": 0,
                "erros": [error_msg],
                "detalhe": {
                    "fase": "03_download",
                    "id_orcamento": id_orcamento,
                    "status": "erro",
                    "erro": str(e)
                }
            }

    # ================================================================
    # ORQUESTRADOR — PROCESSAMENTO COMPLETO
    # ================================================================

    @staticmethod
    async def processar_orcamento_com_distribuicao(
        db: Session, 
        request: FluxoOrcamentoRequest
    ) -> ProcessamentoResultado:
        """
        Orquestra o processamento completo de orçamentos com distribuição.
        
        Fluxo:
        1. Gera orçamentos locais via SQL (agrupa por escola/unidade)
        2. Para cada orçamento, executa as fases configuradas:
           - FASE 01: Envia para API Bremen e salva em orcamento_api
           - FASE 02: Aprova na API Bremen e salva em aprovacao_api (se configurado)
           - FASE 03: Baixa e organiza arquivos PDF (se configurado)
        
        Args:
            db: Sessão do banco de dados
            request: Dados para processamento
            
        Returns:
            ProcessamentoResultado com detalhes do processamento
        """
        logger.info(f"Iniciando processamento completo para escola {request.escola_id}")

        resultado = ProcessamentoResultado(
            total=0, enviados=0, aprovados=0,
            salvos=0, downloads=0, erros=[], detalhes=[]
        )

        api_service = OrcamentoAPIService()
        modo = getattr(request, 'modo_agrupamento', 'unidade')

        # Gerar grupo_lote_id sequencial a partir do banco de dados
        grupo_lote_id = getattr(request, 'grupo_lote_id', None)
        if not grupo_lote_id:
            try:
                db.execute(text("CREATE SEQUENCE IF NOT EXISTS lote_id_seq START WITH 1 INCREMENT BY 1"))
                row = db.execute(text("SELECT nextval('lote_id_seq')")).fetchone()
                grupo_lote_id = row[0]
                logger.info(f"Grupo lote ID gerado sequencialmente: {grupo_lote_id}")
            except Exception as e:
                logger.warning(f"Erro ao gerar lote_id via sequence: {e}")
                import random
                grupo_lote_id = random.randint(100000, 999999)

        resultado.grupo_lote_id = grupo_lote_id

        try:
            # Gerar orçamentos locais (via SQL)
            orcamentos_locais = await OrcamentoController.gerar_orcamento(
                db,
                OrcamentoRequest(
                    escola_id=request.escola_id,
                    ids_produtos=request.ids_produtos,
                    datas_saida=request.datas_saida,
                    divisoes_logistica=request.divisoes_logistica,
                    dias_uteis_filtro=request.dias_uteis_filtro,
                    ids_formularios=request.ids_formularios,
                    status_ids=request.status_ids,
                    modo_agrupamento=modo
                )
            )

            resultado.total = orcamentos_locais.total_unidades
            logger.info(f"Orçamentos gerados localmente: {resultado.total} unidade(s)")

            if not orcamentos_locais.orcamentos:
                logger.warning("Lista de orçamentos está vazia! Nenhum orçamento será enviado à API.")
                resultado.erros.append("Nenhum orçamento gerado para enviar à API")
                return resultado

            # Processar cada orçamento sequencialmente
            total_orcamentos = len(orcamentos_locais.orcamentos)

            for idx, orcamento in enumerate(orcamentos_locais.orcamentos):
                if idx > 0:
                    logger.info(f"Aguardando {DELAY_ENTRE_ORCAMENTOS}s antes da próxima requisição...")
                    await asyncio.sleep(DELAY_ENTRE_ORCAMENTOS)

                logger.info(f"Enviando orçamento {idx + 1}/{total_orcamentos} para API Bremen...")

                try:
                    # FASE 01 — Enviar orçamento
                    fase01 = await OrcamentoController._fase01_enviar_orcamento(
                        db, api_service, orcamento, modo, grupo_lote_id=grupo_lote_id
                    )
                    resultado.enviados += 1
                    resultado.salvos += 1
                    resultado.detalhes.append(fase01["detalhe"])

                    id_orcamento = fase01["id_orcamento"]
                    distribuicoes_ids = fase01["distribuicoes_ids"]
                    payload_enviado = fase01["payload_enviado"]

                    # FASE 02 — Aprovar orçamento (se configurado)
                    if request.data_entrega and request.aprovar_automaticamente:
                        gerar_op = getattr(request, 'gerar_op', True)
                        fase02 = await OrcamentoController._fase02_aprovar_orcamento(
                            db, api_service, id_orcamento,
                            request.data_entrega, distribuicoes_ids, payload_enviado,
                            gerar_op=gerar_op,
                            grupo_lote_id=grupo_lote_id
                        )
                        resultado.detalhes.append(fase02["detalhe"])

                        if fase02["aprovado"]:
                            resultado.aprovados += 1
                        else:
                            resultado.erros.append(fase02["erro"])

                    # FASE 03 — Baixar arquivos (somente se aprovado E com OP gerada)
                    if request.baixar_arquivos and fase02.get("tem_op", False):
                        fase03 = await OrcamentoController._fase03_baixar_arquivos(
                            db, id_orcamento, distribuicoes_ids, grupo_lote_id=grupo_lote_id
                        )
                        resultado.downloads += fase03["downloads"]
                        resultado.erros.extend(fase03["erros"])
                        resultado.detalhes.append(fase03["detalhe"])

                except Exception as e:
                    error_msg = f"Erro na FASE 01 (orçamento) para índice {idx}: {str(e)}"
                    logger.error(error_msg)
                    resultado.erros.append(error_msg)
                    try:
                        db.rollback()
                        logger.debug(f"Rollback realizado após erro no orçamento {idx}")
                    except Exception:
                        pass

            # Log final
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
