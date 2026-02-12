from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict, Any, Optional
from ..models.orcamento_api import OrcamentoAPI
from ..models.aprovacao_api import AprovacaoAPI
from ..models.distribuicao_material import DistribuicaoMaterial
from ..models.historico_processamento import HistoricoProcessamento
from ..models.status_deskflow_pedido import StatusDeskflowPedido
from ..schemas.orcamento import (
    OrcamentoRequest, OrcamentoListResponse, OrcamentoData, 
    OrcamentoResponse, ItemOrcamento
)
from ..config.logging_config import get_logger
from pathlib import Path


logger = get_logger(__name__)


class OrcamentoService:
    """Service para operações de orçamento"""
    
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
            sql_file = Path(__file__).parent / 'sql' / sql_filename
            with open(sql_file, 'r', encoding='utf-8') as f:
                query_sql = f.read()
            
            # Preparar parâmetros
            params = {
                'escola_id': request.escola_id,
                'ids_produtos': request.ids_produtos,
                'datas_saida': [d.isoformat() for d in request.datas_saida],
                'divisoes_logistica': request.divisoes_logistica,
                'dias_uteis_filtro': request.dias_uteis_filtro
            }
            
            # Executar query
            result = db.execute(text(query_sql), params)
            orcamentos_raw = result.fetchall()
            
            # Processar resultados
            orcamentos = []
            for row in orcamentos_raw:
                orcamento_data = row[0]  # JSON do orçamento
                
                # Converter para schema
                data = OrcamentoData(
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
        payload_enviado: Dict[str, Any]
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
            
            registros_salvos = []
            BATCH_SIZE = 5
            batch_count = 0
            
            for i, item_resposta in enumerate(itens_resposta):
                id_item = item_resposta.get('id')
                if not id_item:
                    logger.warning(f"Item sem ID encontrado no índice {i}, pulando: {item_resposta}")
                    continue
                
                # Buscar id_distribuicao do item correspondente no payload enviado
                id_distribuicao = None
                if i < len(itens_payload):
                    item_payload = itens_payload[i]
                    
                    # Primeiro tentar ids_distribuicao no item (modo escola)
                    ids_dist = item_payload.get('ids_distribuicao')
                    if ids_dist and isinstance(ids_dist, list) and len(ids_dist) > 0:
                        # Modo escola: usar o primeiro ID como distribuicao principal
                        id_distribuicao = ids_dist[0]
                        logger.debug(f"Modo escola - Item {i}: usando primeiro id_distribuicao={id_distribuicao} de {len(ids_dist)} distribuições")
                    else:
                        # Modo unidade: buscar em componentes[0].id_distribuicao
                        componentes = item_payload.get('componentes', [])
                        if componentes:
                            id_distribuicao = componentes[0].get('id_distribuicao')
                
                if not id_distribuicao:
                    logger.error(f"id_distribuicao não encontrado para item índice {i} (id_item={id_item})")
                    raise ValueError(f"id_distribuicao não encontrado para item índice {i}")
                
                # Deletar registro existente para evitar duplicatas
                db.query(OrcamentoAPI).filter(
                    OrcamentoAPI.distribuicao_material_id == id_distribuicao,
                    OrcamentoAPI.id_orcamento == id_orcamento,
                    OrcamentoAPI.id_item == id_item
                ).delete(synchronize_session=False)
                
                # Criar novo registro para este item
                orcamento_api = OrcamentoAPI(
                    distribuicao_material_id=id_distribuicao,
                    id_orcamento=id_orcamento,
                    id_item=id_item,
                    itens=item_resposta,  # Um objeto por registro, não array
                    resposta_api=resposta_completa
                )
                db.add(orcamento_api)
                registros_salvos.append(orcamento_api)
                batch_count += 1
                
                logger.debug(
                    f"Item {i}: distribuicao_material_id={id_distribuicao}, "
                    f"id_item={id_item}, descricao={item_resposta.get('descricao', '')[:50]}"
                )
                
                # Commit a cada BATCH_SIZE registros
                if batch_count >= BATCH_SIZE:
                    db.commit()
                    logger.debug(f"Commit em lote - {len(registros_salvos)}/{len(itens_resposta)} registros salvos")
                    batch_count = 0
            
            # Commit final para registros restantes
            if batch_count > 0:
                db.commit()
            
            # Refresh dos registros
            for registro in registros_salvos:
                try:
                    db.refresh(registro)
                except Exception:
                    pass
            
            logger.info(
                f"Orçamento API salvo com sucesso - {len(registros_salvos)} registros criados "
                f"para orçamento {id_orcamento}"
            )
            return registros_salvos
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar orçamento API para distribuição {distribuicao_id}: {str(e)}")
            raise
    
    @staticmethod
    def salvar_aprovacao_api(
        db: Session, 
        id_orcamento: int,
        resposta_completa: Dict[str, Any],
        distribuicoes_ids: List[int],
        payload_orcamento: Dict[str, Any]
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
            
            # Extrair pedido (um único objeto)
            pedidos_lista = data_item.get('pedidos', [])
            pedido = pedidos_lista[0] if pedidos_lista else {}
            
            logger.info(f"OPs extraídas: {ops}, Pedido: {pedido}")
            logger.info(f"Distribuições IDs para correspondência: {distribuicoes_ids}")
            
            if not ops:
                raise ValueError(f"Nenhuma OP encontrada na resposta de aprovação do orçamento {id_orcamento}")
            
            # Deletar registros existentes para este orçamento
            db.query(AprovacaoAPI).filter(
                AprovacaoAPI.id_orcamento == id_orcamento
            ).delete(synchronize_session=False)
            db.commit()
            
            registros_salvos = []
            
            # Criar uma linha por OP com correspondência sequencial aos distribuicoes_ids
            for i, op_id in enumerate(ops):
                # Correspondência sequencial: OP[i] → distribuicao_id[i]
                if i < len(distribuicoes_ids):
                    dist_id = distribuicoes_ids[i]
                else:
                    logger.warning(
                        f"OP índice {i} (id={op_id}) sem distribuição correspondente. "
                        f"Total OPs: {len(ops)}, Total distribuições: {len(distribuicoes_ids)}"
                    )
                    # Usar o último distribuicao_id disponível como fallback
                    dist_id = distribuicoes_ids[-1] if distribuicoes_ids else None
                    if not dist_id:
                        raise ValueError(f"Nenhuma distribuição disponível para OP {op_id}")
                
                aprovacao = AprovacaoAPI(
                    distribuicao_material_id=dist_id,
                    id_orcamento=id_orcamento,
                    id_ops=op_id,
                    pedidos=pedido,  # Objeto único, não array
                    resposta_api=resposta_completa
                )
                db.add(aprovacao)
                registros_salvos.append(aprovacao)
                
                logger.debug(
                    f"OP {i}: distribuicao_material_id={dist_id}, "
                    f"id_ops={op_id}, pedido={pedido}"
                )
            
            db.commit()
            
            # Refresh dos registros
            for registro in registros_salvos:
                try:
                    db.refresh(registro)
                except Exception:
                    pass
            
            logger.info(
                f"Aprovação API salva com sucesso - {len(registros_salvos)} registros criados "
                f"para orçamento {id_orcamento}"
            )
            return registros_salvos
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar aprovação API para distribuição {distribuicao_id}: {str(e)}")
            raise
    
    @staticmethod
    def atualizar_status_distribuicao(db: Session, distribuicao_id: int, 
                                    novo_status: str, mensagem: str, 
                                    sucesso: bool = True) -> HistoricoProcessamento:
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
            
            # Buscar ou criar status
            status = db.query(StatusDeskflowPedido).filter(
                StatusDeskflowPedido.codigo == novo_status
            ).first()
            
            if not status:
                # Criar novo status se não existir
                status = StatusDeskflowPedido(
                    codigo=novo_status,
                    descricao=f"Status {novo_status}"
                )
                db.add(status)
                db.flush()
            
            # Buscar distribuições relacionadas (capa/miolo do mesmo formulário + unidade)
            distribuicoes_para_atualizar = [distribuicao]
            
            if distribuicao.formulario_id and distribuicao.unidade_escolar_id:
                relacionadas = db.query(DistribuicaoMaterial).filter(
                    DistribuicaoMaterial.formulario_id == distribuicao.formulario_id,
                    DistribuicaoMaterial.unidade_escolar_id == distribuicao.unidade_escolar_id,
                    DistribuicaoMaterial.id != distribuicao_id
                ).all()
                
                if relacionadas:
                    ids_relacionadas = [r.id for r in relacionadas]
                    logger.info(
                        f"Cascata: encontradas {len(relacionadas)} distribuição(ões) "
                        f"relacionada(s) para atualizar em conjunto: {ids_relacionadas}"
                    )
                    distribuicoes_para_atualizar.extend(relacionadas)
            
            # Atualizar todas as distribuições (principal + relacionadas)
            historico_principal = None
            
            for dist in distribuicoes_para_atualizar:
                status_anterior_id = dist.status_id
                
                # Pular se já está no status desejado
                if status_anterior_id == status.id:
                    logger.debug(
                        f"Distribuição {dist.id} já está no status {novo_status}, "
                        f"pulando atualização"
                    )
                    continue
                
                # Atualizar status
                dist.status_id = status.id
                
                # Criar histórico
                msg = mensagem if dist.id == distribuicao_id else (
                    f"[Cascata] {mensagem} (atualizado junto com distribuição #{distribuicao_id})"
                )
                
                historico = HistoricoProcessamento(
                    distribuicao_material_id=dist.id,
                    status_anterior_id=status_anterior_id,
                    status_novo_id=status.id,
                    mensagem=msg,
                    sucesso=sucesso
                )
                db.add(historico)
                
                if dist.id == distribuicao_id:
                    historico_principal = historico
                
                logger.info(f"Status da distribuição {dist.id} atualizado para {novo_status}")
            
            # Commit atômico de todas as atualizações
            db.commit()
            
            if historico_principal:
                db.refresh(historico_principal)
                logger.info(
                    f"Status atualizado para {novo_status} — "
                    f"{len(distribuicoes_para_atualizar)} distribuição(ões) afetada(s), "
                    f"histórico principal ID {historico_principal.id}"
                )
                return historico_principal
            
            # Fallback: se a distribuição principal já estava no status correto
            return None
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao atualizar status da distribuição {distribuicao_id}: {str(e)}")
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

