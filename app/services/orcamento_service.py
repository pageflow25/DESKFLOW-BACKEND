from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict, Any, Optional
import json
from datetime import date
from ..models.orcamento_api import OrcamentoAPI
from ..models.aprovacao_api import AprovacaoAPI
from ..models.distribuicao_material import DistribuicaoMaterial
from ..models.historico_processamento import HistoricoProcessamento
from ..models.status_deskflow_pedido import StatusDeskflowPedido
from ..schemas.orcamento import OrcamentoRequest, OrcamentoListResponse, OrcamentoData, OrcamentoResponse, ItemOrcamento
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
    def salvar_orcamento_api(db: Session, distribuicao_id: int, id_orcamento: int, 
                           itens: List[Dict[str, Any]], resposta_completa: Dict[str, Any]) -> OrcamentoAPI:
        """
        Salva o retorno da API de orçamento na tabela orcamento_api
        
        Args:
            db: Sessão do banco de dados
            distribuicao_id: ID da distribuição de material
            id_orcamento: ID do orçamento retornado pela API
            itens: Lista de itens do orçamento
            resposta_completa: Resposta completa da API
            
        Returns:
            OrcamentoAPI: Registro salvo
        """
        logger.info(f"Salvando orçamento API para distribuição {distribuicao_id}")
        
        try:
            # Verificar se já existe um orçamento para esta distribuição
            existing = db.query(OrcamentoAPI).filter(
                OrcamentoAPI.distribuicao_material_id == distribuicao_id
            ).first()
            
            if existing:
                # Atualizar existente
                existing.id_orcamento = id_orcamento
                existing.itens = itens
                existing.resposta_api = resposta_completa
                orcamento_api = existing
            else:
                # Criar novo
                orcamento_api = OrcamentoAPI(
                    distribuicao_material_id=distribuicao_id,
                    id_orcamento=id_orcamento,
                    itens=itens,
                    resposta_api=resposta_completa
                )
                db.add(orcamento_api)
            
            db.commit()
            db.refresh(orcamento_api)
            
            logger.info(f"Orçamento API salvo com ID {orcamento_api.id}")
            return orcamento_api
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar orçamento API: {str(e)}")
            raise
    
    @staticmethod
    def salvar_aprovacao_api(db: Session, distribuicao_id: int, id_orcamento: int, 
                            id_ops: Optional[int], pedidos: List[Dict[str, Any]], 
                            resposta_completa: Dict[str, Any]) -> AprovacaoAPI:
        """
        Salva o retorno da API de aprovação na tabela aprovacao_api
        
        Args:
            db: Sessão do banco de dados
            distribuicao_id: ID da distribuição de material
            id_orcamento: ID do orçamento aprovado
            id_ops: ID das OPs geradas
            pedidos: Lista de pedidos gerados
            resposta_completa: Resposta completa da API
            
        Returns:
            AprovacaoAPI: Registro salvo
        """
        logger.info(f"Salvando aprovação API para distribuição {distribuicao_id}")
        
        try:
            # Verificar se já existe uma aprovação para esta distribuição
            existing = db.query(AprovacaoAPI).filter(
                AprovacaoAPI.distribuicao_material_id == distribuicao_id
            ).first()
            
            if existing:
                # Atualizar existente
                existing.id_orcamento = id_orcamento
                existing.id_ops = id_ops
                existing.pedidos = pedidos
                existing.resposta_api = resposta_completa
                aprovacao_api = existing
            else:
                # Criar novo
                aprovacao_api = AprovacaoAPI(
                    distribuicao_material_id=distribuicao_id,
                    id_orcamento=id_orcamento,
                    id_ops=id_ops,
                    pedidos=pedidos,
                    resposta_api=resposta_completa
                )
                db.add(aprovacao_api)
            
            db.commit()
            db.refresh(aprovacao_api)
            
            logger.info(f"Aprovação API salva com ID {aprovacao_api.id}")
            return aprovacao_api
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar aprovação API: {str(e)}")
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
