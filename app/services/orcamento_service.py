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
            # Carregar query SQL
            sql_file = Path(__file__).parent / 'sql' / 'query_orcamento.sql'
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
                    id_vendedor=orcamento_data['data']['id_vendedor'],
                    id_forma_pagamento=orcamento_data['data']['id_forma_pagamento'],
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
        distribuicao_id: int, 
        id_orcamento: int, 
        itens: List[Dict[str, Any]], 
        resposta_completa: Dict[str, Any],
        payload_enviado: Optional[Dict[str, Any]] = None
    ) -> List[OrcamentoAPI]:
        """
        Salva o retorno da API de orçamento na tabela orcamento_api
        
        IMPORTANTE: Cada item do orçamento gera uma linha separada na tabela.
        O id_orcamento se repete para cada item do mesmo orçamento.
        
        Persiste o vínculo entre:
        - Distribuição de material
        - Orçamento gerado na API
        - Item específico do orçamento
        - Resposta completa da API
        
        Args:
            db: Sessão do banco de dados
            distribuicao_id: ID da distribuição de material
            id_orcamento: ID do orçamento retornado pela API
            itens: Lista de itens do orçamento (cada item contém id, descricao, quantidade)
            resposta_completa: Resposta completa da API de criação de orçamento
            payload_enviado: Payload original enviado para a API (opcional, para debug)
            
        Returns:
            List[OrcamentoAPI]: Lista de registros salvos (um por item)
            
        Exemplo de inserts gerados:
            INSERT INTO orcamento_api (distribuicao_material_id, id_orcamento, id_item, itens, resposta_api)
            VALUES (123, 2322, 26439, '{"id": 26439, "descricao": "...", "quantidade": 60}', '...');
            
            INSERT INTO orcamento_api (distribuicao_material_id, id_orcamento, id_item, itens, resposta_api)
            VALUES (123, 2322, 26440, '{"id": 26440, "descricao": "...", "quantidade": 65}', '...');
        """
        logger.info(f"Salvando orçamento API para distribuição {distribuicao_id}")
        logger.debug(f"ID Orçamento: {id_orcamento}, Qtd Itens: {len(itens)}")
        
        try:
            # Validar dados de entrada
            if not distribuicao_id or distribuicao_id <= 0:
                raise ValueError(f"ID de distribuição inválido: {distribuicao_id}")
            
            if not id_orcamento or id_orcamento <= 0:
                raise ValueError(f"ID de orçamento inválido: {id_orcamento}")
            
            # Garantir que itens é uma lista válida
            itens_lista = itens if isinstance(itens, list) else []
            
            if not itens_lista:
                logger.warning(f"Nenhum item para salvar no orçamento {id_orcamento}")
                return []
            
            registros_salvos = []
            
            # Deletar registros existentes para esta distribuição e orçamento (para evitar duplicatas)
            db.query(OrcamentoAPI).filter(
                OrcamentoAPI.distribuicao_material_id == distribuicao_id,
                OrcamentoAPI.id_orcamento == id_orcamento
            ).delete(synchronize_session=False)
            
            # Criar um registro para cada item
            for item in itens_lista:
                id_item = item.get('id')
                
                if not id_item:
                    logger.warning(f"Item sem ID encontrado, pulando: {item}")
                    continue
                
                # Criar novo registro para este item
                orcamento_api = OrcamentoAPI(
                    distribuicao_material_id=distribuicao_id,
                    id_orcamento=id_orcamento,
                    id_item=id_item,
                    itens=item,  # Um objeto por registro, não uma lista
                    resposta_api=resposta_completa
                )
                db.add(orcamento_api)
                registros_salvos.append(orcamento_api)
                
                logger.debug(f"Registro criado para item {id_item}")
            
            db.commit()
            
            # Refresh dos registros
            for registro in registros_salvos:
                db.refresh(registro)
            
            logger.info(f"Orçamento API salvo com sucesso - {len(registros_salvos)} registros criados para orçamento {id_orcamento}")
            return registros_salvos
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar orçamento API para distribuição {distribuicao_id}: {str(e)}")
            raise
    
    @staticmethod
    def salvar_aprovacao_api(
        db: Session, 
        distribuicao_id: int, 
        id_orcamento: int, 
        id_ops: Optional[int], 
        pedidos: List[Dict[str, Any]], 
        resposta_completa: Dict[str, Any],
        itens_enviados: Optional[List[Dict[str, Any]]] = None
    ) -> AprovacaoAPI:
        """
        Salva o retorno da API de aprovação na tabela aprovacao_api
        
        Persiste o vínculo entre:
        - Distribuição de material
        - Orçamento aprovado
        - OPs geradas
        - Pedidos criados
        - Resposta completa da API
        
        Args:
            db: Sessão do banco de dados
            distribuicao_id: ID da distribuição de material
            id_orcamento: ID do orçamento aprovado
            id_ops: ID das OPs (Ordens de Produção) geradas
            pedidos: Lista de pedidos gerados na aprovação
            resposta_completa: Resposta completa da API de aprovação
            itens_enviados: Itens enviados para aprovação (id + data_entrega)
            
        Returns:
            AprovacaoAPI: Registro salvo
            
        Exemplo de payload de aprovação enviado:
            {
                "identifier": "PageFlow",
                "data": {
                    "id_orcamento": 21893,
                    "gerar_op": true,
                    "itens": [
                        {"id": 21893, "data_entrega": "2026-01-15T12:00:00.000-03:00"}
                    ]
                }
            }
        """
        logger.info(f"Salvando aprovação API para distribuição {distribuicao_id}")
        logger.debug(f"ID Orçamento: {id_orcamento}, ID OPs: {id_ops}, Pedidos: {len(pedidos)}")
        
        try:
            # Validar dados de entrada
            if not distribuicao_id or distribuicao_id <= 0:
                raise ValueError(f"ID de distribuição inválido: {distribuicao_id}")
            
            # Preparar pedidos para JSONB - garantir que é uma lista válida
            pedidos_json = pedidos if isinstance(pedidos, list) else []
            
            # Verificar se já existe uma aprovação para esta distribuição
            existing = db.query(AprovacaoAPI).filter(
                AprovacaoAPI.distribuicao_material_id == distribuicao_id
            ).first()
            
            if existing:
                # Atualizar existente
                logger.info(f"Atualizando aprovação existente ID {existing.id}")
                existing.id_orcamento = id_orcamento
                existing.id_ops = id_ops
                existing.pedidos = pedidos_json
                existing.resposta_api = resposta_completa
                aprovacao_api = existing
            else:
                # Criar novo registro
                aprovacao_api = AprovacaoAPI(
                    distribuicao_material_id=distribuicao_id,
                    id_orcamento=id_orcamento,
                    id_ops=id_ops,
                    pedidos=pedidos_json,
                    resposta_api=resposta_completa
                )
                db.add(aprovacao_api)
                logger.info(f"Criando novo registro de aprovação para distribuição {distribuicao_id}")
            
            db.commit()
            db.refresh(aprovacao_api)
            
            logger.info(f"Aprovação API salva com sucesso - ID registro: {aprovacao_api.id}, ID OPs: {id_ops}")
            return aprovacao_api
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar aprovação API para distribuição {distribuicao_id}: {str(e)}")
            raise
    
    @staticmethod
    def atualizar_status_distribuicao(db: Session, distribuicao_id: int, 
                                    novo_status: str, mensagem: str, 
                                    sucesso: bool = True) -> HistoricoProcessamento:
        """
        Atualiza o status de uma distribuição e salva no histórico
        
        Args:
            db: Sessão do banco de dados
            distribuicao_id: ID da distribuição
            novo_status: Novo status
            mensagem: Mensagem do evento
            sucesso: Se a operação foi bem-sucedida
            
        Returns:
            HistoricoProcessamento: Registro de histórico criado
        """
        logger.info(f"Atualizando status da distribuição {distribuicao_id} para {novo_status}")
        
        try:
            # Buscar distribuição
            distribuicao = db.query(DistribuicaoMaterial).filter(
                DistribuicaoMaterial.id == distribuicao_id
            ).first()
            
            if not distribuicao:
                raise ValueError(f"Distribuição {distribuicao_id} não encontrada")
            
            status_anterior = distribuicao.status_id
            
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
            
            # Atualizar status da distribuição
            distribuicao.status_id = status.id
            
            # Criar histórico
            historico = HistoricoProcessamento(
                distribuicao_material_id=distribuicao_id,
                status_anterior=status_anterior,
                status_novo=novo_status,
                mensagem=mensagem,
                sucesso=sucesso
            )
            
            db.add(historico)
            db.commit()
            db.refresh(historico)
            
            logger.info(f"Status atualizado para {novo_status} - histórico ID {historico.id}")
            return historico
            
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
        logger.info(f"Buscando distribuições para escola {escola_id}")
        
        try:
            query = """
                SELECT DISTINCT dm.*
                FROM distribuicao_materiais dm
                JOIN unidades_escolares ue ON ue.id = dm.unidade_escolar_id
                JOIN especificacoes_form ef ON ef.id = dm.especificacao_form_id
                WHERE ue.escola_id = :escola_id
                AND ef.id_produto = ANY(:ids_produtos)
                AND dm.quantidade > 0
            """
            
            result = db.execute(text(query), {
                'escola_id': escola_id,
                'ids_produtos': ids_produtos
            })
            
            distribuicoes = []
            for row in result:
                distribuicao = db.query(DistribuicaoMaterial).filter(
                    DistribuicaoMaterial.id == row.id
                ).first()
                if distribuicao:
                    distribuicoes.append(distribuicao)
            
            logger.info(f"Encontradas {len(distribuicoes)} distribuições")
            return distribuicoes
            
        except Exception as e:
            logger.error(f"Erro ao buscar distribuições: {str(e)}")
            raise
