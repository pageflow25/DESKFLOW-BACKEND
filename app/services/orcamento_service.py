from sqlalchemy.orm import Session
from sqlalchemy import text, update
from typing import List, Dict, Any, Optional
from ..models.orcamento_api import OrcamentoAPI
from ..models.aprovacao_api import AprovacaoAPI
from ..models.distribuicao_material import DistribuicaoMaterial
from ..models.historico_processamento import HistoricoProcessamento
from ..models.status_deskflow_pedido import StatusDeskflowPedido
from ..models.lote_envio import LoteEnvio
from ..models.envio_item import EnvioItem
from ..models.unidade_escolar import UnidadeEscolar
from ..models.arquivo_pdf import ArquivoPdf
from ..schemas.orcamento import (
    OrcamentoRequest, OrcamentoListResponse, OrcamentoData, 
    OrcamentoResponse, ItemOrcamento
)
from ..config.logging_config import get_logger
from pathlib import Path
from datetime import datetime


logger = get_logger(__name__)

# Diretório do SQL
SQL_DIR = Path(__file__).parent / "sql"


def _carregar_query(nome_arquivo: str) -> str:
    """Carrega uma query SQL de um arquivo."""
    caminho = SQL_DIR / nome_arquivo
    return caminho.read_text(encoding="utf-8-sig")


class OrcamentoService:
    """Service para operações de orçamento"""

    # ================================================================
    # CACHE DE STATUS — armazena apenas o ID (int) para evitar objetos
    # ORM detached entre sessões diferentes
    # ================================================================
    _status_cache: Dict[str, int] = {}

    @staticmethod
    def garantir_colunas_lote_envio(db: Session) -> None:
        db.execute(
            text(
                "ALTER TABLE lote_envio "
                "ADD COLUMN IF NOT EXISTS organizacao_arquivos VARCHAR(40) NOT NULL DEFAULT 'por_op'"
            )
        )
        db.flush()

    @classmethod
    def _get_or_create_status(cls, db: Session, codigo: str) -> int:
        """
        Retorna o ID do StatusDeskflowPedido pelo código.
        Usa cache em memória (apenas o ID, nunca o objeto ORM) para evitar
        erros de instância detached entre sessões diferentes.
        """
        if codigo in cls._status_cache:
            return cls._status_cache[codigo]

        status = db.query(StatusDeskflowPedido).filter(
            StatusDeskflowPedido.codigo == codigo
        ).first()

        if not status:
            status = StatusDeskflowPedido(
                codigo=codigo,
                descricao=f"Status {codigo}"
            )
            db.add(status)
            db.flush()
            logger.info(f"Novo status criado: {codigo}")

        cls._status_cache[codigo] = status.id
        return status.id

    @classmethod
    def _invalidar_cache_status(cls):
        """Limpa o cache de status (usar quando dados de status são alterados externamente)."""
        cls._status_cache.clear()

    @staticmethod
    def obter_ou_criar_lote_envio(db: Session, grupo_lote_id: Optional[int]) -> LoteEnvio:
        """Garante existência do lote canônico para a execução atual."""
        if grupo_lote_id is None:
            raise ValueError("grupo_lote_id é obrigatório para criar/obter lote_envio")

        OrcamentoService.garantir_colunas_lote_envio(db)

        lote = db.query(LoteEnvio).filter(
            LoteEnvio.legacy_grupo_lote_id == grupo_lote_id
        ).first()

        if lote:
            return lote

        lote = LoteEnvio(
            identificador_lote=f"LEGACY-{grupo_lote_id}",
            legacy_grupo_lote_id=grupo_lote_id,
            status="em_processamento",
        )
        db.add(lote)
        db.flush()
        return lote

    @staticmethod
    def obter_ou_criar_envio_itens(
        db: Session,
        lote_envio_id: int,
        distribuicoes_ids: List[int],
    ) -> Dict[int, int]:
        """
        Garante um envio_item por distribuição dentro do lote e retorna mapa
        distribuicao_material_id -> envio_item_id.
        """
        if not distribuicoes_ids:
            return {}

        if not lote_envio_id:
            raise ValueError("lote_envio_id é obrigatório para criar/obter envio_item")

        ids_unicos = sorted(set(distribuicoes_ids))

        existentes = db.query(
            EnvioItem.id,
            EnvioItem.distribuicao_material_id,
        ).filter(
            EnvioItem.lote_envio_id == lote_envio_id,
            EnvioItem.distribuicao_material_id.in_(ids_unicos),
        ).all()

        mapa: Dict[int, int] = {row.distribuicao_material_id: row.id for row in existentes}
        faltantes = [did for did in ids_unicos if did not in mapa]

        if faltantes:
            distribuicoes = db.query(
                DistribuicaoMaterial.id,
                DistribuicaoMaterial.formulario_id,
                DistribuicaoMaterial.id_orcamento,
                DistribuicaoMaterial.id_ops,
                DistribuicaoMaterial.descricao_material,
                UnidadeEscolar.escola_id,
            ).outerjoin(
                UnidadeEscolar,
                UnidadeEscolar.id == DistribuicaoMaterial.unidade_escolar_id,
            ).filter(DistribuicaoMaterial.id.in_(faltantes)).all()

            payload = []
            for d in distribuicoes:
                payload.append(
                    {
                        "lote_envio_id": lote_envio_id,
                        "distribuicao_material_id": d.id,
                        "status_envio": "em_processamento",
                        "sucesso_ultimo_evento": False,
                        "id_orcamento_snapshot": d.id_orcamento,
                        "id_ops_snapshot": d.id_ops,
                        "arquivo_nome_snapshot": d.descricao_material,
                        "escola_id_snapshot": d.escola_id,
                        "formulario_id_snapshot": d.formulario_id,
                    }
                )

            if payload:
                db.bulk_insert_mappings(EnvioItem, payload)
                db.flush()

            recarregados = db.query(
                EnvioItem.id,
                EnvioItem.distribuicao_material_id,
            ).filter(
                EnvioItem.lote_envio_id == lote_envio_id,
                EnvioItem.distribuicao_material_id.in_(ids_unicos),
            ).all()
            mapa = {row.distribuicao_material_id: row.id for row in recarregados}

        return mapa

    @staticmethod
    def atualizar_snapshot_envio_itens(
        db: Session,
        envio_item_ids_por_distribuicao: Dict[int, int],
        id_orcamento: Optional[int] = None,
        id_ops_por_distribuicao: Optional[Dict[int, int]] = None,
        status_envio: Optional[str] = None,
        sucesso_ultimo_evento: Optional[bool] = None,
    ) -> None:
        """Atualiza snapshots do envio_item após cada fase do fluxo."""
        if not envio_item_ids_por_distribuicao:
            return

        for dist_id, envio_item_id in envio_item_ids_por_distribuicao.items():
            valores: Dict[str, Any] = {}

            if id_orcamento is not None:
                valores["id_orcamento_snapshot"] = id_orcamento

            if id_ops_por_distribuicao and dist_id in id_ops_por_distribuicao:
                valores["id_ops_snapshot"] = id_ops_por_distribuicao[dist_id]

            if status_envio is not None:
                valores["status_envio"] = status_envio

            if sucesso_ultimo_evento is not None:
                valores["sucesso_ultimo_evento"] = sucesso_ultimo_evento

            if not valores:
                continue

            db.execute(
                update(EnvioItem)
                .where(EnvioItem.id == envio_item_id)
                .values(**valores)
            )
    
    @staticmethod
    def gerar_orcamento(db: Session, request: OrcamentoRequest) -> OrcamentoListResponse:
        """
        Gera orçamento com base nos filtros fornecidos usando a query SQL
        
        Args:
            db: Sessão do banco de dados
            request: Dados para geração do orçamento
            
        Returns:
            OrcamentoListResponse com orçamentos gerados
        """
        logger.info(f"Iniciando geração de orçamento para escola {request.escola_id}")
        
        try:
            # Escolher query SQL com base no modo de agrupamento
            modo = getattr(request, 'modo_agrupamento', 'unidade')
            if modo == 'escola':
                sql_filename = 'query_orcamento_escola.sql'
                logger.info(f"Modo ESCOLA: agrupando todas as unidades da escola {request.escola_id}")
            else:
                sql_filename = 'query_orcamento.sql'
                logger.info(f"Modo UNIDADE: gerando orçamento por unidade para escola {request.escola_id}")
            
            # Carregar query SQL
            query_sql = _carregar_query(sql_filename)
            
            # Preparar parâmetros
            params = {
                'escola_id': request.escola_id,
                'ids_produtos': request.ids_produtos,
                'datas_saida': [d.isoformat() for d in request.datas_saida],
                'divisoes_logistica': request.divisoes_logistica,
                'dias_uteis_filtro': request.dias_uteis_filtro,
                'ids_formularios': getattr(request, 'ids_formularios', None),
                'status_ids': getattr(request, 'status_ids', None) or [1],
                'ids_unidades': getattr(request, 'ids_unidades', None),
                'ids_arquivos': getattr(request, 'ids_arquivos', None),
                'nome_arquivo_filtro': getattr(request, 'nome_arquivo_filtro', None),
            }

            logger.info(f"Parâmetros da query ({sql_filename}): {params}")

            # Executar query
            result = db.execute(text(query_sql), params)
            orcamentos_raw = result.fetchall()

            logger.info(f"Query retornou {len(orcamentos_raw)} linhas")
            
            # Processar resultados
            orcamentos = []
            for row in orcamentos_raw:
                orcamento_data = row[0]  # JSON do orçamento
                
                # Converter para schema
                data = OrcamentoData(
                    id_escola=orcamento_data['data'].get('id_escola'),
                    nome_unidade=orcamento_data['data'].get('nome_unidade'),
                    id_cliente=orcamento_data['data'].get('id_cliente'),
                    id_vendedor=orcamento_data['data'].get('id_vendedor') or 2285,
                    id_forma_pagamento=str(orcamento_data['data']['id_forma_pagamento']),  # Converter para string
                    itens=[ItemOrcamento(**item) for item in orcamento_data['data']['itens']]
                )
                
                orcamento = OrcamentoResponse(
                    identifier=orcamento_data['identifier'],
                    data=data
                )
                
                orcamentos.append(orcamento)
            
            logger.info(f"Gerados {len(orcamentos)} orçamentos para escola {request.escola_id}")
            
            return OrcamentoListResponse(
                orcamentos=orcamentos,
                total_unidades=len(orcamentos),
                mensagem=f"Gerados {len(orcamentos)} orçamentos com sucesso"
            )
            
        except Exception as e:
            logger.error(f"Erro ao gerar orçamento para escola {request.escola_id}: {str(e)}")
            raise
    
    @staticmethod
    def salvar_orcamento_api(
        db: Session, 
        id_orcamento: int, 
        itens_resposta: List[Dict[str, Any]], 
        resposta_completa: Dict[str, Any],
        payload_enviado: Dict[str, Any],
        envio_item_ids_por_distribuicao: Dict[int, int],
    ) -> List[OrcamentoAPI]:
        """
        Salva o retorno da API de orçamento na tabela orcamento_api
        
        IMPORTANTE: Cada item do orçamento gera uma linha separada na tabela.
        O distribuicao_material_id vem de componentes[0].id_distribuicao do payload enviado.
        A correspondência é sequencial: itens[i] do request ↔ itens[i] do response.
        
        Args:
            db: Sessão do banco de dados
            id_orcamento: ID do orçamento retornado pela API
            itens_resposta: Lista de itens retornados pela API (cada item contém id, descricao, quantidade)
            resposta_completa: Resposta completa da API de criação de orçamento
            payload_enviado: Payload original enviado para a API (contém id_distribuicao nos componentes)
            
        Returns:
            List[OrcamentoAPI]: Lista de registros salvos (um por item)
        """
        logger.info(f"Salvando orçamento API - ID: {id_orcamento}, Itens: {len(itens_resposta)}")

        try:
            if not id_orcamento or id_orcamento <= 0:
                raise ValueError(f"ID de orçamento inválido: {id_orcamento}")

            if not itens_resposta:
                logger.warning(f"Nenhum item para salvar no orçamento {id_orcamento}")
                return []

            # Extrair id_distribuicao de cada item do payload enviado (correspondência sequencial)
            # Modo unidade: id_distribuicao está em componentes[0].id_distribuicao
            # Modo escola: ids_distribuicao está no item (array de IDs)
            itens_payload = payload_enviado.get('data', {}).get('itens', [])

            # --- PASSO 1: montar todos os registros em memória (sem I/O) ---
            mappings: List[Dict[str, Any]] = []

            for i, item_resposta in enumerate(itens_resposta):
                id_item = item_resposta.get('id')
                if not id_item:
                    logger.warning(f"Item sem ID encontrado no índice {i}, pulando: {item_resposta}")
                    continue

                id_distribuicao = None
                if i < len(itens_payload):
                    item_payload = itens_payload[i]
                    ids_dist = item_payload.get('ids_distribuicao')
                    if ids_dist and isinstance(ids_dist, list) and len(ids_dist) > 0:
                        id_distribuicao = ids_dist[0]
                        logger.debug(
                            f"Modo escola - Item {i}: usando primeiro id_distribuicao={id_distribuicao} "
                            f"de {len(ids_dist)} distribuições"
                        )
                    else:
                        componentes = item_payload.get('componentes', [])
                        if componentes:
                            id_distribuicao = componentes[0].get('id_distribuicao')

                if not id_distribuicao:
                    logger.error(f"id_distribuicao não encontrado para item índice {i} (id_item={id_item})")
                    raise ValueError(f"id_distribuicao não encontrado para item índice {i}")

                envio_item_id = envio_item_ids_por_distribuicao.get(id_distribuicao)
                if not envio_item_id:
                    raise ValueError(
                        f"envio_item_id não encontrado para distribuicao_material_id={id_distribuicao}"
                    )

                mappings.append({
                    "distribuicao_material_id": id_distribuicao,
                    "id_orcamento": id_orcamento,
                    "id_item": id_item,
                    "itens": item_resposta,
                    "resposta_api": resposta_completa,
                    "envio_item_id": envio_item_id,
                })
                logger.debug(
                    f"Item {i}: distribuicao_material_id={id_distribuicao}, "
                    f"id_item={id_item}, descricao={item_resposta.get('descricao', '')[:50]}"
                )

            # --- PASSO 2: delete único para todos os itens deste orçamento ---
            ids_item = [m["id_item"] for m in mappings]
            ids_distribuicao = list({m["distribuicao_material_id"] for m in mappings})
            db.query(OrcamentoAPI).filter(
                OrcamentoAPI.id_orcamento == id_orcamento,
                OrcamentoAPI.id_item.in_(ids_item)
            ).delete(synchronize_session=False)

            # --- PASSO 2.1: sincronizar o id_orcamento na distribuição para consultas legadas/monitor ---
            db.query(DistribuicaoMaterial).filter(
                DistribuicaoMaterial.id.in_(ids_distribuicao)
            ).update(
                {DistribuicaoMaterial.id_orcamento: id_orcamento},
                synchronize_session=False
            )

            # --- PASSO 3: bulk insert em uma única operação ---
            db.bulk_insert_mappings(OrcamentoAPI, mappings)
            db.commit()

            # Buscar os registros inseridos para retornar (uma única query)
            registros_salvos = db.query(OrcamentoAPI).filter(
                OrcamentoAPI.id_orcamento == id_orcamento,
                OrcamentoAPI.id_item.in_(ids_item)
            ).all()

            logger.info(
                f"Orçamento API salvo com sucesso - {len(registros_salvos)} registros criados "
                f"para orçamento {id_orcamento}"
            )
            return registros_salvos

        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar orçamento API (id_orcamento={id_orcamento}): {str(e)}")
            raise
    
    @staticmethod
    def salvar_aprovacao_api(
        db: Session, 
        id_orcamento: int,
        resposta_completa: Dict[str, Any],
        distribuicoes_ids: List[int],
        payload_orcamento: Dict[str, Any],
        envio_item_ids_por_distribuicao: Dict[int, int],
    ) -> List[AprovacaoAPI]:
        """
        Salva o retorno da API de aprovação na tabela aprovacao_api
        
        IMPORTANTE: Cada OP gera uma linha separada na tabela.
        As OPs vêm como string separada por vírgula em data[0].id_ops.
        A correspondência é sequencial: OPs[i] ↔ distribuicoes_ids[i].
        
        Args:
            db: Sessão do banco de dados
            id_orcamento: ID do orçamento aprovado
            resposta_completa: Resposta completa da API de aprovação
            distribuicoes_ids: Lista de id_distribuicao na mesma ordem do request original
            payload_orcamento: Payload original do orçamento (para extrair id_distribuicao se necessário)
            
        Returns:
            List[AprovacaoAPI]: Lista de registros salvos (um por OP)
        """
        logger.info(f"Salvando aprovação API para orçamento {id_orcamento}")

        try:
            # Extrair dados da resposta
            data = resposta_completa.get('data', [])
            if isinstance(data, list) and len(data) > 0:
                data_item = data[0]
            elif isinstance(data, dict):
                data_item = data
            else:
                raise ValueError(f"Formato de resposta de aprovação inesperado: {type(data)}")

            # Extrair OPs (vem como string separada por vírgula)
            id_ops_str = data_item.get('id_ops', '')
            if isinstance(id_ops_str, str):
                ops = [int(op.strip()) for op in id_ops_str.split(',') if op.strip()]
            elif isinstance(id_ops_str, int):
                ops = [id_ops_str]
            else:
                ops = []

            # Extrair pedido/PV. A API pode retornar um array em `pedidos`, um objeto,
            # ou o identificador direto no próprio item de data.
            pedidos_lista = data_item.get('pedidos', [])
            if isinstance(pedidos_lista, list):
                pedido = pedidos_lista[0] if pedidos_lista else {}
            elif isinstance(pedidos_lista, dict):
                pedido = pedidos_lista
            else:
                pedido = {}

            id_pedido_venda = (
                data_item.get('id_pedido_venda')
                or data_item.get('id_pedido')
                or data_item.get('pedido_venda')
                or data_item.get('id_pv')
                or (pedido.get('id') if pedido else None)
                or (pedido.get('id_pedido_venda') if pedido else None)
                or (pedido.get('id_pedido') if pedido else None)
            )
            if isinstance(id_pedido_venda, dict):
                id_pedido_venda = (
                    id_pedido_venda.get('id')
                    or id_pedido_venda.get('id_pedido_venda')
                    or id_pedido_venda.get('id_pedido')
                )

            logger.info(f"OPs extraídas: {ops}, Pedido: {pedido}, id_pedido_venda: {id_pedido_venda}")
            logger.info(f"Distribuições IDs para correspondência: {distribuicoes_ids}")

            # --- PASSO 1: montar todos os mappings em memória ---
            mappings: List[Dict[str, Any]] = []

            if ops:
                itens_payload = payload_orcamento.get('data', {}).get('itens', [])
                distribuicoes_por_op: List[List[int]] = []

                for item_payload in itens_payload:
                    ids_dist = item_payload.get('ids_distribuicao')
                    if ids_dist and isinstance(ids_dist, list):
                        distribuicoes_por_op.append(ids_dist)
                        continue

                    componentes = item_payload.get('componentes', [])
                    dist_id = componentes[0].get('id_distribuicao') if componentes else None
                    if dist_id:
                        distribuicoes_por_op.append([dist_id])

                pares_op_distribuicao: List[tuple[int, int]] = []
                if len(distribuicoes_por_op) == len(ops):
                    for op_id, ids_item in zip(ops, distribuicoes_por_op):
                        for dist_id in ids_item:
                            pares_op_distribuicao.append((op_id, dist_id))
                else:
                    logger.warning(
                        "Não foi possível mapear OPs por item; usando correspondência sequencial. "
                        f"OPs={len(ops)}, itens_payload={len(distribuicoes_por_op)}, "
                        f"distribuições={len(distribuicoes_ids)}"
                    )
                    for i, op_id in enumerate(ops):
                        if i < len(distribuicoes_ids):
                            dist_id = distribuicoes_ids[i]
                        else:
                            dist_id = distribuicoes_ids[-1] if distribuicoes_ids else None
                            if not dist_id:
                                raise ValueError(f"Nenhuma distribuição disponível para OP {op_id}")
                        pares_op_distribuicao.append((op_id, dist_id))

                for i, (op_id, dist_id) in enumerate(pares_op_distribuicao):
                    envio_item_id = envio_item_ids_por_distribuicao.get(dist_id)
                    if not envio_item_id:
                        raise ValueError(
                            f"envio_item_id não encontrado para distribuicao_material_id={dist_id}"
                        )

                    mappings.append({
                        "distribuicao_material_id": dist_id,
                        "id_orcamento": id_orcamento,
                        "id_ops": op_id,
                        "id_pedido_venda": id_pedido_venda,
                        "pedidos": pedido,
                        "resposta_api": resposta_completa,
                        "envio_item_id": envio_item_id,
                    })
                    logger.debug(f"OP {i}: distribuicao_material_id={dist_id}, id_ops={op_id}")
            else:
                # Sem OP (gerar_op=False) — salva apenas o pedido de venda, id_ops=None
                logger.info(
                    f"Orçamento {id_orcamento} sem OP (apenas pedido de venda). "
                    f"Salvando {len(distribuicoes_ids)} registro(s) com id_ops=None."
                )
                for dist_id in distribuicoes_ids:
                    envio_item_id = envio_item_ids_por_distribuicao.get(dist_id)
                    if not envio_item_id:
                        raise ValueError(
                            f"envio_item_id não encontrado para distribuicao_material_id={dist_id}"
                        )
                    mappings.append({
                        "distribuicao_material_id": dist_id,
                        "id_orcamento": id_orcamento,
                        "id_ops": None,
                        "id_pedido_venda": id_pedido_venda,
                        "pedidos": pedido,
                        "resposta_api": resposta_completa,
                        "envio_item_id": envio_item_id,
                    })

            # --- PASSO 2: delete único + bulk insert em uma única transação ---
            db.query(AprovacaoAPI).filter(
                AprovacaoAPI.id_orcamento == id_orcamento
            ).delete(synchronize_session=False)

            db.bulk_insert_mappings(AprovacaoAPI, mappings)
            db.commit()

            # Buscar os registros inseridos (uma única query)
            registros_salvos = db.query(AprovacaoAPI).filter(
                AprovacaoAPI.id_orcamento == id_orcamento
            ).all()

            logger.info(
                f"Aprovação API salva com sucesso - {len(registros_salvos)} registros criados "
                f"para orçamento {id_orcamento} "
                f"({'com OP' if ops else 'sem OP — apenas pedido de venda'})"
            )
            return registros_salvos

        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar aprovação API (id_orcamento={id_orcamento}): {str(e)}")
            raise
    
    @staticmethod
    def atualizar_status_distribuicao(db: Session, distribuicao_id: int, 
                                    novo_status: str, mensagem: str, 
                                    sucesso: bool = True,
                                    lote_envio_id: Optional[int] = None,
                                    envio_item_id: Optional[int] = None) -> HistoricoProcessamento:
        """
        Atualiza o status de uma distribuição e salva no histórico.
        
        CASCATA: Também atualiza automaticamente todas as distribuições
        relacionadas (capa/miolo) que compartilham o mesmo formulario_id 
        e unidade_escolar_id, mantendo-as sempre sincronizadas.
        
        Args:
            db: Sessão do banco de dados
            distribuicao_id: ID da distribuição
            novo_status: Novo status
            mensagem: Mensagem do evento
            sucesso: Se a operação foi bem-sucedida
            
        Returns:
            HistoricoProcessamento: Registro de histórico criado para a distribuição principal
        """
        logger.info(f"Atualizando status da distribuição {distribuicao_id} para {novo_status}")

        try:
            # Buscar distribuição principal
            distribuicao = db.query(DistribuicaoMaterial).filter(
                DistribuicaoMaterial.id == distribuicao_id
            ).first()

            if not distribuicao:
                raise ValueError(f"Distribuição {distribuicao_id} não encontrada")

            # Obter status via cache (evita query repetida a cada chamada)
            status = OrcamentoService._get_or_create_status(db, novo_status)

            # Coletar todos os IDs a atualizar (principal + relacionadas por capa/miolo)
            todos_ids = [distribuicao_id]
            status_anteriores: Dict[int, Optional[int]] = {distribuicao_id: distribuicao.status_id}

            if distribuicao.formulario_id and distribuicao.unidade_escolar_id:
                # Cascata por escopo: mesmo formulário + unidade + turma.
                # Distribuições sem turma (id_turma IS NULL) cascatam apenas entre si.
                relacionadas_query = db.query(
                    DistribuicaoMaterial.id,
                    DistribuicaoMaterial.status_id
                ).filter(
                    DistribuicaoMaterial.formulario_id == distribuicao.formulario_id,
                    DistribuicaoMaterial.unidade_escolar_id == distribuicao.unidade_escolar_id,
                    DistribuicaoMaterial.id != distribuicao_id
                )

                if distribuicao.id_turma is None:
                    relacionadas_query = relacionadas_query.filter(
                        DistribuicaoMaterial.id_turma.is_(None)
                    )
                else:
                    relacionadas_query = relacionadas_query.filter(
                        DistribuicaoMaterial.id_turma == distribuicao.id_turma
                    )

                relacionadas = relacionadas_query.all()

                if relacionadas:
                    ids_relacionadas = [r.id for r in relacionadas]
                    logger.info(
                        f"Cascata: {len(relacionadas)} distribuição(ões) relacionada(s): "
                        f"{ids_relacionadas}"
                    )
                    todos_ids.extend(ids_relacionadas)
                    for r in relacionadas:
                        status_anteriores[r.id] = r.status_id

            # Filtrar somente os que precisam mudar (excluir os que já estão no status alvo)
            ids_para_atualizar = [
                i for i in todos_ids
                if status_anteriores.get(i) != status
            ]

            if not ids_para_atualizar:
                logger.debug(f"Todas as distribuições já estão no status {novo_status}, nada a fazer")
                return None

            # Bulk UPDATE em uma única query SQL
            db.execute(
                update(DistribuicaoMaterial)
                .where(DistribuicaoMaterial.id.in_(ids_para_atualizar))
                .values(status_id=status)
            )

            # Garantir envio_item canônico para escrita do histórico
            if envio_item_id is None:
                if lote_envio_id is None:
                    identificador_manual = f"MANUAL-{distribuicao_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
                    lote_manual = LoteEnvio(
                        identificador_lote=identificador_manual,
                        status="manual",
                        data_envio=datetime.utcnow(),
                    )
                    db.add(lote_manual)
                    db.flush()
                    lote_envio_id = lote_manual.id

                mapa_envio = OrcamentoService.obter_ou_criar_envio_itens(
                    db=db,
                    lote_envio_id=lote_envio_id,
                    distribuicoes_ids=ids_para_atualizar,
                )
            else:
                mapa_envio = {dist_id: envio_item_id for dist_id in ids_para_atualizar}

            # Criar os registros de histórico em bulk
            historicos: List[Dict[str, Any]] = []
            for dist_id in ids_para_atualizar:
                msg = mensagem if dist_id == distribuicao_id else (
                    f"[Cascata] {mensagem} (atualizado junto com distribuição #{distribuicao_id})"
                )
                historicos.append({
                    "distribuicao_material_id": dist_id,
                    "status_anterior_id": status_anteriores.get(dist_id),
                    "status_novo_id": status,
                    "mensagem": msg,
                    "sucesso": sucesso,
                    "envio_item_id": mapa_envio.get(dist_id),
                })

            db.bulk_insert_mappings(HistoricoProcessamento, historicos)
            db.commit()

            # Buscar o histórico principal para retorno
            historico_principal = db.query(HistoricoProcessamento).filter(
                HistoricoProcessamento.distribuicao_material_id == distribuicao_id,
                HistoricoProcessamento.status_novo_id == status,
            ).order_by(HistoricoProcessamento.data_evento.desc()).first()

            logger.info(
                f"Status atualizado para {novo_status} — "
                f"{len(ids_para_atualizar)} distribuição(ões) afetada(s)"
            )
            return historico_principal

        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao atualizar status da distribuição {distribuicao_id}: {str(e)}")
            raise

    @staticmethod
    def atualizar_status_em_lote(
        db: Session,
        distribuicoes_ids: List[int],
        novo_status: str,
        mensagem: str,
        sucesso: bool = True,
        grupo_lote_id: int = None,
        lote_envio_id: Optional[int] = None,
        envio_item_ids_por_distribuicao: Optional[Dict[int, int]] = None,
    ) -> int:
        """
        Atualiza o status de múltiplas distribuições em uma única operação
        de banco de dados (sem loop de transações individuais).

        Inclui a lógica de cascata capa/miolo para cada distribuição informada.

        Args:
            db: Sessão do banco de dados
            distribuicoes_ids: Lista de IDs de distribuição a atualizar
            novo_status: Código do novo status
            mensagem: Mensagem descritiva do evento
            sucesso: Se a operação foi bem-sucedida
            grupo_lote_id: ID do grupo selecionado ao disparar o lote (opcional)

        Returns:
            Número de distribuições efetivamente atualizadas
        """
        if not distribuicoes_ids:
            return 0

        logger.info(
            f"Atualização em lote: {len(distribuicoes_ids)} distribuições → {novo_status}"
        )

        try:
            # Obter status via cache
            status = OrcamentoService._get_or_create_status(db, novo_status)

            # -- 1. Buscar todas as distribuições informadas + pares do arquivo (capa/miolo) --
            from sqlalchemy import and_, or_
            from sqlalchemy.orm import aliased

            ArquivoBase = aliased(ArquivoPdf)
            distribuicoes_base = db.query(
                DistribuicaoMaterial.id,
                DistribuicaoMaterial.status_id,
                DistribuicaoMaterial.formulario_id,
                DistribuicaoMaterial.unidade_escolar_id,
                DistribuicaoMaterial.especificacao_form_id,
                DistribuicaoMaterial.id_turma,
                ArquivoBase.pares.label('pares'),
            ).outerjoin(
                ArquivoBase,
                ArquivoBase.id == DistribuicaoMaterial.arquivo_pdf_id,
            ).filter(DistribuicaoMaterial.id.in_(distribuicoes_ids)).all()

            if not distribuicoes_base:
                logger.warning(f"Nenhuma distribuição encontrada para os IDs: {distribuicoes_ids}")
                return 0

            # IDs já conhecidos (os passados + suas cascatas)
            todos_ids_set: set = set(distribuicoes_ids)
            status_map: Dict[int, Optional[int]] = {d.id: d.status_id for d in distribuicoes_base}

            # Cascata: agrupar por pares (vincula capa↔miolo do mesmo livro).
            # Distribuições sem pares usam especificacao_form_id como chave — evita
            # expansão indevida para livros diferentes na mesma (form, unidade, turma).
            grupos_pares: list = []    # (form_id, unit_id, turma, pares_val)
            conds_sem_pares: list = [] # condições SQLAlchemy para distribuições sem pares

            for d in distribuicoes_base:
                if not (d.formulario_id and d.unidade_escolar_id):
                    continue
                turma_cond = (
                    DistribuicaoMaterial.id_turma.is_(None)
                    if d.id_turma is None
                    else DistribuicaoMaterial.id_turma == d.id_turma
                )
                if d.pares is not None:
                    grupos_pares.append((d.formulario_id, d.unidade_escolar_id, d.id_turma, d.pares))
                else:
                    conds_sem_pares.append(and_(
                        DistribuicaoMaterial.formulario_id == d.formulario_id,
                        DistribuicaoMaterial.unidade_escolar_id == d.unidade_escolar_id,
                        DistribuicaoMaterial.especificacao_form_id == d.especificacao_form_id,
                        turma_cond,
                    ))

            # Cascata 1 — por pares (capa ↔ miolo)
            if grupos_pares:
                ArquivoCascata = aliased(ArquivoPdf)
                pares_conds = []
                for form_id, unit_id, turma, pares_val in grupos_pares:
                    turma_cond = (
                        DistribuicaoMaterial.id_turma.is_(None)
                        if turma is None
                        else DistribuicaoMaterial.id_turma == turma
                    )
                    pares_conds.append(and_(
                        DistribuicaoMaterial.formulario_id == form_id,
                        DistribuicaoMaterial.unidade_escolar_id == unit_id,
                        turma_cond,
                        ArquivoCascata.pares == pares_val,
                    ))
                relacionadas_pares = db.query(
                    DistribuicaoMaterial.id,
                    DistribuicaoMaterial.status_id,
                ).join(
                    ArquivoCascata,
                    ArquivoCascata.id == DistribuicaoMaterial.arquivo_pdf_id,
                ).filter(
                    or_(*pares_conds),
                    DistribuicaoMaterial.id.notin_(distribuicoes_ids),
                ).all()
                for r in relacionadas_pares:
                    todos_ids_set.add(r.id)
                    status_map[r.id] = r.status_id
                    logger.debug(f"Cascata (pares): incluindo distribuição {r.id}")

            # Cascata 2 — por especificação (sem pares)
            if conds_sem_pares:
                relacionadas_spec = db.query(
                    DistribuicaoMaterial.id,
                    DistribuicaoMaterial.status_id,
                ).filter(
                    or_(*conds_sem_pares),
                    DistribuicaoMaterial.id.notin_(distribuicoes_ids),
                ).all()
                for r in relacionadas_spec:
                    todos_ids_set.add(r.id)
                    status_map[r.id] = r.status_id
                    logger.debug(f"Cascata (spec): incluindo distribuição {r.id}")

            # -- 2. Filtrar somente os que precisam mudar --
            ids_para_atualizar = [
                i for i in todos_ids_set
                if status_map.get(i) != status
            ]

            if not ids_para_atualizar:
                logger.debug(f"Todas as distribuições já estão no status {novo_status}")
                return 0

            # -- 3. Bulk UPDATE em uma única query --
            mapa_envio_item: Dict[int, int] = envio_item_ids_por_distribuicao or {}

            if lote_envio_id is None and grupo_lote_id is not None:
                lote = OrcamentoService.obter_ou_criar_lote_envio(db, grupo_lote_id)
                lote_envio_id = lote.id

            if lote_envio_id is not None:
                mapa_envio_item = OrcamentoService.obter_ou_criar_envio_itens(
                    db=db,
                    lote_envio_id=lote_envio_id,
                    distribuicoes_ids=ids_para_atualizar,
                )

            faltando_envio_item = [dist_id for dist_id in ids_para_atualizar if dist_id not in mapa_envio_item]
            if faltando_envio_item:
                lote_fallback = LoteEnvio(
                    identificador_lote=f"BULK-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
                    status="manual",
                    data_envio=datetime.utcnow(),
                )
                db.add(lote_fallback)
                db.flush()

                mapa_fallback = OrcamentoService.obter_ou_criar_envio_itens(
                    db=db,
                    lote_envio_id=lote_fallback.id,
                    distribuicoes_ids=faltando_envio_item,
                )
                mapa_envio_item.update(mapa_fallback)

            if grupo_lote_id is not None:
                # Raw SQL para atualizar grupo_id E acumular grupo_lote_ids
                # sem sobrescrever o histórico de lotes anteriores
                db.execute(
                    text("""
                        UPDATE pedido_distribuicoes
                        SET
                            status_id = :status_id,
                            grupo_id  = :lote_id,
                            grupo_lote_ids = CASE
                                WHEN grupo_lote_ids IS NULL OR grupo_lote_ids = ''
                                    THEN jsonb_build_array(:lote_id)::text
                                WHEN grupo_lote_ids::jsonb @> jsonb_build_array(:lote_id)
                                    THEN grupo_lote_ids
                                ELSE
                                    (grupo_lote_ids::jsonb || jsonb_build_array(:lote_id))::text
                            END
                        WHERE id = ANY(:ids)
                    """),
                    {
                        "status_id": status,
                        "lote_id": grupo_lote_id,
                        "ids": ids_para_atualizar,
                    },
                )
            else:
                db.execute(
                    update(DistribuicaoMaterial)
                    .where(DistribuicaoMaterial.id.in_(ids_para_atualizar))
                    .values(status_id=status)
                )

            # -- 4. Bulk INSERT de histórico --
            ids_originais = set(distribuicoes_ids)
            historicos: List[Dict[str, Any]] = []
            for dist_id in ids_para_atualizar:
                if dist_id in ids_originais:
                    msg = mensagem
                else:
                    msg = f"[Cascata] {mensagem}"
                hist_entry = {
                    "distribuicao_material_id": dist_id,
                    "status_anterior_id": status_map.get(dist_id),
                    "status_novo_id": status,
                    "mensagem": msg,
                    "sucesso": sucesso,
                    "envio_item_id": mapa_envio_item.get(dist_id),
                }
                if grupo_lote_id is not None:
                    hist_entry["grupo_lote_id"] = grupo_lote_id
                historicos.append(hist_entry)

            db.bulk_insert_mappings(HistoricoProcessamento, historicos)
            db.commit()

            logger.info(
                f"Lote concluído: {len(ids_para_atualizar)} distribuições → {novo_status} "
                f"({len(ids_para_atualizar) - len(distribuicoes_ids)} via cascata)"
            )
            return len(ids_para_atualizar)

        except Exception as e:
            db.rollback()
            logger.error(f"Erro na atualização em lote de status: {str(e)}")
            raise

    @staticmethod
    def obter_distribuicoes_por_escola(db: Session, escola_id: int, 
                                     ids_produtos: List[int]) -> List[DistribuicaoMaterial]:
        """
        Obtém distribuições de material filtradas por escola e produtos
        
        Args:
            db: Sessão do banco de dados
            escola_id: ID da escola
            ids_produtos: Lista de IDs de produtos
            
        Returns:
            List[DistribuicaoMaterial]: Lista de distribuições
        """
        from ..models.unidade_escolar import UnidadeEscolar
        from ..models.especificacao_form import EspecificacaoForm
        
        logger.info(f"Buscando distribuições para escola {escola_id} com produtos {ids_produtos}")
        
        try:
            # Usar ORM diretamente com join - uma única query
            distribuicoes = db.query(DistribuicaoMaterial).join(
                UnidadeEscolar, UnidadeEscolar.id == DistribuicaoMaterial.unidade_escolar_id
            ).join(
                EspecificacaoForm, EspecificacaoForm.id == DistribuicaoMaterial.especificacao_form_id
            ).filter(
                UnidadeEscolar.escola_id == escola_id,
                EspecificacaoForm.id_produto.in_(ids_produtos),
                DistribuicaoMaterial.quantidade > 0
            ).distinct().all()
            
            logger.info(f"Encontradas {len(distribuicoes)} distribuições")
            return distribuicoes
            
        except Exception as e:
            logger.error(f"Erro ao buscar distribuições: {str(e)}")
            raise

