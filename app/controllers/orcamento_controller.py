import asyncio
import json
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List, Optional
from ..config.logging_config import get_logger
from ..schemas.orcamento import (
    OrcamentoRequest, 
    OrcamentoListResponse, 
    OrcamentoResponse,
    FluxoOrcamentoRequest, 
    ProcessamentoResultado,
    LoteDisparoListResponse,
    LoteDisparoResumo,
    LoteDisparoEvento,
    LoteDisparoOP,
    LoteDisparoDistribuicao,
    LoteDisparoOrcamento,
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
    _execucao_lock = asyncio.Lock()
    _execucoes_em_andamento: set[str] = set()

    @staticmethod
    def _agora_banco(db: Session) -> datetime:
        """Obtém o horário corrente do banco para manter consistência de timezone."""
        return db.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()

    @staticmethod
    def _gerar_chave_execucao(request: FluxoOrcamentoRequest) -> str:
        payload_chave = {
            "tipo_fluxo": request.tipo_fluxo,
            "escola_id": request.escola_id,
            "ids_produtos": sorted(request.ids_produtos or []),
            "datas_saida": sorted([d.isoformat() for d in (request.datas_saida or [])]),
            "divisoes_logistica": sorted(request.divisoes_logistica or []),
            "dias_uteis_filtro": sorted(request.dias_uteis_filtro or []),
            "ids_formularios": sorted(request.ids_formularios or []),
            "status_ids": sorted(request.status_ids or []),
            "modo_agrupamento": request.modo_agrupamento,
            "grupo_lote_id": request.grupo_lote_id,
            "aprovar_automaticamente": request.aprovar_automaticamente,
            "baixar_arquivos": request.baixar_arquivos,
            "gerar_op": request.gerar_op,
            "usar_data_saida_distribuicao": request.usar_data_saida_distribuicao,
            "persistir_resultado": request.persistir_resultado,
        }
        return json.dumps(payload_chave, ensure_ascii=False, sort_keys=True)

    @staticmethod
    async def listar_lotes_disparo(db: Session, limit: int = 10, offset: int = 0) -> LoteDisparoListResponse:
        """Lista lotes agrupados: Lote → Orçamento → Distribuição → OPs. Suporta paginação."""
        limite = max(1, min(limit, 100))
        deslocamento = max(0, offset)

        # Contagem total de lotes distintos
        total_row = (
            db.execute(
                text(
                    "SELECT COUNT(DISTINCT grupo_lote_id) AS total "
                    "FROM historico_processamento WHERE grupo_lote_id IS NOT NULL"
                )
            )
            .mappings()
            .one()
        )
        total_geral = int(total_row["total"] or 0)

        # Lotes paginados (resumo)
        lotes_rows = (
            db.execute(
                text(
                    """
                    SELECT
                        hp.grupo_lote_id,
                        MIN(hp.data_evento) AS data_envio,
                        COUNT(DISTINCT hp.distribuicao_material_id) AS total_pedidos,
                        COUNT(DISTINCT CASE WHEN hp.sucesso IS TRUE THEN hp.distribuicao_material_id END) AS total_sucesso,
                        COUNT(DISTINCT CASE WHEN hp.sucesso IS FALSE THEN hp.distribuicao_material_id END) AS total_erro
                    FROM historico_processamento hp
                    WHERE hp.grupo_lote_id IS NOT NULL
                    GROUP BY hp.grupo_lote_id
                    ORDER BY hp.grupo_lote_id DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limite, "offset": deslocamento},
            )
            .mappings()
            .all()
        )

        lotes: List[LoteDisparoResumo] = []

        for lote in lotes_rows:
            grupo_lote_id = int(lote["grupo_lote_id"])

            # --- Eventos (timeline) por distribuição ---
            eventos_rows = (
                db.execute(
                    text(
                        """
                        SELECT
                            hp.distribuicao_material_id,
                            COALESCE(s.codigo, 'desconhecido') AS status_codigo,
                            hp.sucesso,
                            hp.mensagem,
                            hp.data_evento
                        FROM historico_processamento hp
                        LEFT JOIN status_deskflow_pedido s ON s.id = hp.status_novo_id
                        WHERE hp.grupo_lote_id = :grupo_lote_id
                        ORDER BY hp.distribuicao_material_id, hp.data_evento DESC, hp.id DESC
                        """
                    ),
                    {"grupo_lote_id": grupo_lote_id},
                )
                .mappings()
                .all()
            )

            eventos_por_dist: Dict[int, List[LoteDisparoEvento]] = {}
            for ev in eventos_rows:
                did = int(ev["distribuicao_material_id"])
                eventos_por_dist.setdefault(did, []).append(
                    LoteDisparoEvento(
                        status=str(ev["status_codigo"]),
                        sucesso=bool(ev["sucesso"]),
                        mensagem=ev["mensagem"],
                        data_evento=ev["data_evento"].isoformat() if ev["data_evento"] else None,
                    )
                )

            # --- Distribuições com informações completas ---
            dist_rows = (
                db.execute(
                    text(
                        """
                        WITH ultimos AS (
                            SELECT DISTINCT ON (hp.distribuicao_material_id)
                                hp.distribuicao_material_id,
                                hp.status_novo_id,
                                hp.sucesso,
                                hp.mensagem,
                                hp.data_evento
                            FROM historico_processamento hp
                            WHERE hp.grupo_lote_id = :grupo_lote_id
                            ORDER BY hp.distribuicao_material_id, hp.data_evento DESC, hp.id DESC
                        )
                        SELECT
                            u.distribuicao_material_id,
                            COALESCE(s.codigo, 'desconhecido') AS status_codigo,
                            u.sucesso,
                            u.mensagem,
                            u.data_evento,
                            e.nome AS escola_nome,
                            ue.nome AS unidade_nome,
                            dm.descricao_material AS material_descricao,
                            dm.quantidade,
                            COALESCE(oa_agg.id_orcamento, dm.id_orcamento) AS id_orcamento,
                            ap.nome AS arquivo_nome
                        FROM ultimos u
                        LEFT JOIN status_deskflow_pedido s ON s.id = u.status_novo_id
                        LEFT JOIN pedido_distribuicoes dm ON dm.id = u.distribuicao_material_id
                        LEFT JOIN (
                            SELECT
                                distribuicao_material_id,
                                MAX(id_orcamento) AS id_orcamento
                            FROM orcamento_api
                            WHERE id_orcamento IS NOT NULL
                            GROUP BY distribuicao_material_id
                        ) oa_agg ON oa_agg.distribuicao_material_id = dm.id
                        LEFT JOIN escola_unidades ue ON ue.id = dm.unidade_escolar_id
                        LEFT JOIN escola_escolas e ON e.id = ue.escola_id
                        LEFT JOIN pedido_arquivos_pdf ap ON ap.id = dm.arquivo_pdf_id
                        ORDER BY dm.id_orcamento NULLS LAST, u.distribuicao_material_id
                        """
                    ),
                    {"grupo_lote_id": grupo_lote_id},
                )
                .mappings()
                .all()
            )

            # --- OPs por distribuição ---
            ops_por_dist: Dict[int, List[LoteDisparoOP]] = {}
            ops_rows = (
                db.execute(
                    text(
                        """
                        SELECT
                            aa.distribuicao_material_id,
                            aa.id_ops,
                            aa.pedidos
                        FROM aprovacao_api aa
                        WHERE aa.id_ops IS NOT NULL
                          AND aa.distribuicao_material_id IN (
                              SELECT DISTINCT hp.distribuicao_material_id
                              FROM historico_processamento hp
                              WHERE hp.grupo_lote_id = :grupo_lote_id
                          )
                        ORDER BY aa.distribuicao_material_id, aa.id_ops
                        """
                    ),
                    {"grupo_lote_id": grupo_lote_id},
                )
                .mappings()
                .all()
            )
            for op in ops_rows:
                did = int(op["distribuicao_material_id"])
                ops_por_dist.setdefault(did, []).append(
                    LoteDisparoOP(
                        id_ops=int(op["id_ops"]),
                        pedido=op["pedidos"],
                    )
                )

            # --- Agrupar distribuições por orçamento ---
            orcamentos_map: Dict[Optional[int], List[LoteDisparoDistribuicao]] = {}
            escolas_set: set = set()
            destinos_set: set = set()

            for row in dist_rows:
                did = int(row["distribuicao_material_id"])
                id_orc = row["id_orcamento"]

                if row["escola_nome"]:
                    escolas_set.add(row["escola_nome"])
                if row["unidade_nome"]:
                    destinos_set.add(row["unidade_nome"])

                dist_obj = LoteDisparoDistribuicao(
                    distribuicao_material_id=did,
                    escola_nome=row["escola_nome"],
                    unidade_nome=row["unidade_nome"],
                    material_descricao=row["material_descricao"],
                    arquivo_nome=row["arquivo_nome"],
                    quantidade=row["quantidade"],
                    status=str(row["status_codigo"]),
                    sucesso=bool(row["sucesso"]),
                    mensagem=row["mensagem"],
                    data_evento=row["data_evento"].isoformat() if row["data_evento"] else None,
                    ops=ops_por_dist.get(did, []),
                    eventos=eventos_por_dist.get(did, []),
                )
                orcamentos_map.setdefault(id_orc, []).append(dist_obj)

            orcamentos = [
                LoteDisparoOrcamento(id_orcamento=orc_id, distribuicoes=dists)
                for orc_id, dists in orcamentos_map.items()
            ]

            lotes.append(
                LoteDisparoResumo(
                    grupo_lote_id=grupo_lote_id,
                    data_envio=lote["data_envio"].isoformat() if lote["data_envio"] else None,
                    total_pedidos=int(lote["total_pedidos"] or 0),
                    total_sucesso=int(lote["total_sucesso"] or 0),
                    total_erro=int(lote["total_erro"] or 0),
                    escolas=sorted(escolas_set),
                    destinos=sorted(destinos_set),
                    orcamentos=orcamentos,
                )
            )

        return LoteDisparoListResponse(lotes=lotes, total_lotes=len(lotes), total_geral=total_geral)
    
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
        grupo_lote_id: int = None,
        lote_envio_id: int = None,
        envio_item_ids_por_distribuicao: Dict[int, int] = None,
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
                lote_envio_id=lote_envio_id,
                envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
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
        grupo_lote_id: int = None,
        lote_envio_id: int = None,
        persistir_resultado: bool = True,
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

        envio_item_ids_por_distribuicao: Dict[int, int] = {}
        registros_orcamento = []

        if persistir_resultado:
            envio_item_ids_por_distribuicao = OrcamentoService.obter_ou_criar_envio_itens(
                db=db,
                lote_envio_id=lote_envio_id,
                distribuicoes_ids=distribuicoes_ids,
            )

            # Salvar na tabela orcamento_api (1 registro por item)
            registros_orcamento = OrcamentoService.salvar_orcamento_api(
                db=db,
                id_orcamento=id_orcamento,
                itens_resposta=itens_resposta,
                resposta_completa=resposta_api,
                payload_enviado=payload_enviado,
                envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
            )

            OrcamentoService.atualizar_snapshot_envio_itens(
                db=db,
                envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
                id_orcamento=id_orcamento,
                status_envio="orcamento_gerado",
                sucesso_ultimo_evento=True,
            )

            # Atualizar status das distribuições
            OrcamentoController._atualizar_status_distribuicoes(
                db, distribuicoes_ids,
                novo_status="orcamento_gerado",
                mensagem=f"Orçamento gerado via API - ID: {id_orcamento}",
                grupo_lote_id=grupo_lote_id,
                lote_envio_id=lote_envio_id,
                envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
            )
        else:
            logger.info(
                f"Orçamento {id_orcamento} executado sem persistência: "
                "registros, snapshots e status não serão gravados."
            )

        return {
            "id_orcamento": id_orcamento,
            "distribuicoes_ids": distribuicoes_ids,
            "payload_enviado": payload_enviado,
            "itens_resposta": itens_resposta,
            "detalhe": {
                "fase": "01_orcamento",
                "id_orcamento": id_orcamento,
                "itens_count": len(itens_resposta),
                "registros_criados": len(registros_orcamento),
                "distribuicoes_ids": distribuicoes_ids,
                "persistido": persistir_resultado,
                "status": "sucesso"
            },
            "envio_item_ids_por_distribuicao": envio_item_ids_por_distribuicao,
        }

    # ================================================================
    # FASE 02 — APROVAR ORÇAMENTO NA API BREMEN
    # ================================================================

    @staticmethod
    async def _fase02_aprovar_orcamento(
        db: Session,
        api_service: OrcamentoAPIService,
        id_orcamento: int,
        data_entrega: Optional[str],
        distribuicoes_ids: List[int],
        payload_enviado: Dict[str, Any],
        gerar_op: bool = True,
        usar_data_saida_distribuicao: bool = False,
        grupo_lote_id: int = None,
        lote_envio_id: int = None,
        envio_item_ids_por_distribuicao: Dict[int, int] = None,
        persistir_resultado: bool = True,
        itens_resposta: List[Dict[str, Any]] = None,
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
            if persistir_resultado:
                resposta_aprovacao = await api_service.aprovar_orcamento(
                    db=db,
                    id_orcamento=id_orcamento,
                    data_entrega=data_entrega,
                    gerar_op=gerar_op,
                    usar_data_saida_distribuicao=usar_data_saida_distribuicao,
                )
            else:
                resposta_aprovacao = await api_service.aprovar_orcamento_com_itens(
                    id_orcamento=id_orcamento,
                    itens_resposta=itens_resposta or [],
                    data_entrega=data_entrega,
                    gerar_op=gerar_op,
                )

            # Normalizar resposta (pode vir como lista)
            if isinstance(resposta_aprovacao, list):
                logger.warning("Resposta da aprovação veio como lista. Normalizando.")
                resposta_aprovacao = {"data": resposta_aprovacao}

            data_aprovacao = resposta_aprovacao.get('data', [])
            if isinstance(data_aprovacao, list) and len(data_aprovacao) > 0:
                data_item = data_aprovacao[0]
            elif isinstance(data_aprovacao, dict):
                data_item = data_aprovacao
            else:
                data_item = {}

            id_ops_str = data_item.get('id_ops', '')
            if isinstance(id_ops_str, str):
                ops = [int(op.strip()) for op in id_ops_str.split(',') if op.strip()]
            elif isinstance(id_ops_str, int):
                ops = [id_ops_str]
            else:
                ops = []

            registros_aprovacao = []
            tem_op = len(ops) > 0

            if persistir_resultado:
                # Salvar na tabela aprovacao_api (1 linha por OP, ou 1 por distribuição se sem OP)
                registros_aprovacao = OrcamentoService.salvar_aprovacao_api(
                    db=db,
                    id_orcamento=id_orcamento,
                    resposta_completa=resposta_aprovacao,
                    distribuicoes_ids=distribuicoes_ids,
                    payload_orcamento=payload_enviado,
                    envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
                )

                id_ops_por_distribuicao = {
                    r.distribuicao_material_id: r.id_ops
                    for r in registros_aprovacao
                    if r.id_ops is not None
                }

                OrcamentoService.atualizar_snapshot_envio_itens(
                    db=db,
                    envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
                    id_orcamento=id_orcamento,
                    id_ops_por_distribuicao=id_ops_por_distribuicao,
                    status_envio="orcamento_aprovado" if any(r.id_ops is not None for r in registros_aprovacao) else "pedido_venda_gerado",
                    sucesso_ultimo_evento=True,
                )
            else:
                logger.info(
                    f"Aprovação do orçamento {id_orcamento} executada sem persistência: "
                    "aprovacao_api, snapshots e status não serão gravados."
                )

            if tem_op:
                status_aprovacao = "orcamento_aprovado"
                msg_status = f"Orçamento {id_orcamento} aprovado - {len(ops)} OPs geradas"
            else:
                status_aprovacao = "pedido_venda_gerado"
                msg_status = f"Orçamento {id_orcamento} aprovado como pedido de venda (sem OP)"
                logger.info(f"Orçamento {id_orcamento}: sem OP gerada, FASE 03 será ignorada.")

            if persistir_resultado:
                # Atualizar status das distribuições
                OrcamentoController._atualizar_status_distribuicoes(
                    db, distribuicoes_ids,
                    novo_status=status_aprovacao,
                    mensagem=msg_status,
                    grupo_lote_id=grupo_lote_id,
                    lote_envio_id=lote_envio_id,
                    envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
                )

            return {
                "aprovado": True,
                "tem_op": tem_op,
                "detalhe": {
                    "fase": "02_aprovacao",
                    "id_orcamento": id_orcamento,
                    "ops_count": len(ops),
                    "distribuicoes_ids": distribuicoes_ids,
                    "gerar_op": gerar_op,
                    "persistido": persistir_resultado,
                    "status": "sucesso"
                }
            }

        except Exception as e:
            error_msg = f"Erro na FASE 02 (aprovação) para orçamento {id_orcamento}: {str(e)}"
            logger.error(error_msg)

            if persistir_resultado:
                # Marcar distribuições com erro
                OrcamentoController._atualizar_status_distribuicoes(
                    db, distribuicoes_ids,
                    novo_status="erro_aprovacao",
                    mensagem=error_msg,
                    sucesso=False,
                    grupo_lote_id=grupo_lote_id,
                    lote_envio_id=lote_envio_id,
                    envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
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
        grupo_lote_id: int = None,
        lote_envio_id: int = None,
        envio_item_ids_por_distribuicao: Dict[int, int] = None,
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
                grupo_lote_id=grupo_lote_id,
                lote_envio_id=lote_envio_id,
                envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
            )

            OrcamentoService.atualizar_snapshot_envio_itens(
                db=db,
                envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
                id_orcamento=id_orcamento,
                status_envio="arquivos_baixados",
                sucesso_ultimo_evento=True,
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

        chave_execucao = OrcamentoController._gerar_chave_execucao(request)

        async with OrcamentoController._execucao_lock:
            if chave_execucao in OrcamentoController._execucoes_em_andamento:
                logger.warning(
                    "Processamento duplicado bloqueado para o mesmo lote/filtros em execução"
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Este lote já está em processamento. Aguarde a conclusão antes de reenviar."
                )
            OrcamentoController._execucoes_em_andamento.add(chave_execucao)

        api_service = OrcamentoAPIService()
        modo = getattr(request, 'modo_agrupamento', 'unidade')
        persistir_resultado = getattr(request, 'persistir_resultado', True)
        if not persistir_resultado:
            logger.info("Processamento configurado para executar sem persistir resultados no banco")

        # Gerar grupo_lote_id sequencial a partir do banco de dados
        grupo_lote_id = getattr(request, 'grupo_lote_id', None)
        if persistir_resultado and not grupo_lote_id:
            try:
                db.execute(text("CREATE SEQUENCE IF NOT EXISTS lote_id_seq START WITH 1 INCREMENT BY 1"))
                row = db.execute(text("SELECT nextval('lote_id_seq')")).fetchone()
                grupo_lote_id = row[0]
                logger.info(f"Grupo lote ID gerado sequencialmente: {grupo_lote_id}")
            except Exception as e:
                logger.warning(f"Erro ao gerar lote_id via sequence: {e}")
                import random
                grupo_lote_id = random.randint(100000, 999999)

        resultado.grupo_lote_id = grupo_lote_id if persistir_resultado else None
        lote_envio = None
        lote_envio_id = None

        if persistir_resultado:
            lote_envio = OrcamentoService.obter_ou_criar_lote_envio(db, grupo_lote_id)
            lote_envio_id = lote_envio.id
            agora_lote = OrcamentoController._agora_banco(db)
            lote_envio.status = "em_processamento"
            lote_envio.data_envio = lote_envio.data_envio or agora_lote
            lote_envio.data_ultima_atualizacao = agora_lote
            db.flush()

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
                    ids_unidades=getattr(request, 'ids_unidades', None),
                    ids_arquivos=getattr(request, 'ids_arquivos', None),
                    nome_arquivo_filtro=getattr(request, 'nome_arquivo_filtro', None),
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
                fase02 = {"tem_op": False}

                if idx > 0:
                    logger.info(f"Aguardando {DELAY_ENTRE_ORCAMENTOS}s antes da próxima requisição...")
                    await asyncio.sleep(DELAY_ENTRE_ORCAMENTOS)

                logger.info(f"Enviando orçamento {idx + 1}/{total_orcamentos} para API Bremen...")

                try:
                    # FASE 01 — Enviar orçamento
                    fase01 = await OrcamentoController._fase01_enviar_orcamento(
                        db,
                        api_service,
                        orcamento,
                        modo,
                        grupo_lote_id=grupo_lote_id,
                        lote_envio_id=lote_envio_id,
                        persistir_resultado=persistir_resultado,
                    )
                    resultado.enviados += 1
                    if persistir_resultado:
                        resultado.salvos += 1
                    resultado.detalhes.append(fase01["detalhe"])

                    id_orcamento = fase01["id_orcamento"]
                    distribuicoes_ids = fase01["distribuicoes_ids"]
                    payload_enviado = fase01["payload_enviado"]
                    itens_resposta = fase01["itens_resposta"]
                    envio_item_ids_por_distribuicao = fase01["envio_item_ids_por_distribuicao"]

                    # FASE 02 — Aprovar orçamento (se configurado)
                    usar_data_saida_distribuicao = getattr(request, 'usar_data_saida_distribuicao', False)

                    if request.aprovar_automaticamente and (request.data_entrega or usar_data_saida_distribuicao):
                        gerar_op = getattr(request, 'gerar_op', True)
                        fase02 = await OrcamentoController._fase02_aprovar_orcamento(
                            db, api_service, id_orcamento,
                            request.data_entrega, distribuicoes_ids, payload_enviado,
                            gerar_op=gerar_op,
                            usar_data_saida_distribuicao=usar_data_saida_distribuicao,
                            grupo_lote_id=grupo_lote_id,
                            lote_envio_id=lote_envio_id,
                            envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
                            persistir_resultado=persistir_resultado,
                            itens_resposta=itens_resposta,
                        )
                        resultado.detalhes.append(fase02["detalhe"])

                        if fase02["aprovado"]:
                            resultado.aprovados += 1
                        else:
                            resultado.erros.append(fase02["erro"])

                    # FASE 03 — Baixar arquivos (somente se aprovado E com OP gerada)
                    if persistir_resultado and request.baixar_arquivos and fase02.get("tem_op", False):
                        fase03 = await OrcamentoController._fase03_baixar_arquivos(
                            db,
                            id_orcamento,
                            distribuicoes_ids,
                            grupo_lote_id=grupo_lote_id,
                            lote_envio_id=lote_envio_id,
                            envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
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
                    logger.warning(
                        "Processamento interrompido após erro para evitar envios subsequentes sem OK da API."
                    )
                    break

            # Log final
            logger.info("=" * 60)
            logger.info(
                f"Processamento concluído: {resultado.enviados}/{resultado.total} enviados, "
                f"{resultado.aprovados} aprovados, {len(resultado.erros)} erros"
            )
            if persistir_resultado and lote_envio:
                lote_envio.status = "concluido" if not resultado.erros else "concluido_com_erros"
                lote_envio.data_ultima_atualizacao = OrcamentoController._agora_banco(db)
                db.commit()
            logger.info("=" * 60)

            return resultado

        except Exception as e:
            logger.error(f"Erro geral no processamento: {str(e)}")
            resultado.erros.append(f"Erro geral: {str(e)}")
            try:
                db.rollback()
                if persistir_resultado and lote_envio:
                    lote_envio.status = "erro"
                    lote_envio.data_ultima_atualizacao = OrcamentoController._agora_banco(db)
                    db.commit()
            except Exception as status_error:
                logger.warning(f"Falha ao persistir status de erro do lote: {status_error}")
            return resultado
        finally:
            async with OrcamentoController._execucao_lock:
                OrcamentoController._execucoes_em_andamento.discard(chave_execucao)
