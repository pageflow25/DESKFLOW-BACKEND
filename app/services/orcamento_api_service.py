import httpx
import json
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..models.orcamento_api import OrcamentoAPI
from ..schemas.orcamento import OrcamentoResponse

logger = get_logger(__name__)
settings = get_settings()


class OrcamentoAPIService:
    """Service para integração com APIs externas de orçamento Bremen"""
    
    def __init__(self):
        # URLs das APIs Bremen
        self.api_base_url = settings.BREMEN_API_URL
        self.api_timeout = settings.API_TIMEOUT
        
        # Headers padrão
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': settings.BREMEN_API_TOKEN
        }
    
    async def enviar_orcamento(self, orcamento: OrcamentoResponse) -> Dict[str, Any]:
        """
        Envia orçamento para a API Bremen (FASE 01)
        
        Args:
            orcamento: Dados do orçamento para envio
            
        Returns:
            Dict com resposta da API
        """
        logger.info("Enviando orçamento para API Bremen")
        
        try:
            # Converter orcamento para formato da API
            payload = {
                "identifier": orcamento.identifier,
                "data": {
                    "id_cliente": orcamento.data.id_cliente,
                    "id_vendedor": orcamento.data.id_vendedor,
                    "id_forma_pagamento": orcamento.data.id_forma_pagamento,
                    "itens": [
                        {
                            "id_produto": item.id_produto,
                            "descricao": item.descricao,
                            "quantidade": item.quantidade,
                            "usar_listapreco": item.usar_listapreco,
                            "manter_estrutura_mod_produto": item.manter_estrutura_mod_produto,
                            "componentes": [
                                {
                                    "id": comp.id,
                                    "descricao": comp.descricao,
                                    "altura": comp.altura,
                                    "largura": comp.largura,
                                    "quantidade_paginas": comp.quantidade_paginas,
                                    "gramaturasubstratoimpressao": comp.gramaturasubstratoimpressao,
                                    "corfrente": comp.corfrente,
                                    "corverso": comp.corverso,
                                    "perguntas_componente": comp.perguntas_componente
                                }
                                for comp in item.componentes
                            ],
                            "perguntas_gerais": [
                                {
                                    "tipo": pg.tipo,
                                    "pergunta": pg.pergunta,
                                    "resposta": pg.resposta,
                                    "id_pergunta": pg.id_pergunta
                                }
                                for pg in item.perguntas_gerais
                            ]
                        }
                        for item in orcamento.data.itens
                    ]
                }
            }
            
            # Fazer requisição para API Bremen
            url = f"{self.api_base_url}/api/v1/orcamento"
            
            async with httpx.AsyncClient(timeout=self.api_timeout) as client:
                response = await client.post(
                    url=url,
                    headers=self.headers,
                    json=payload
                )
                
                response.raise_for_status()
                result = response.json()
                
                logger.info(f"Orçamento enviado com sucesso. ID: {result.get('data', {}).get('id_orcamento')}")
                return result
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP ao enviar orçamento: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Erro ao enviar orçamento: {str(e)}")
            raise
    
    async def aprovar_orcamento(
        self, 
        db: Session,
        id_orcamento: int, 
        data_entrega: str,
        gerar_op: bool = True
    ) -> Dict[str, Any]:
        """
        Aprova orçamento na API Bremen (FASE 02)
        
        Busca os itens diretamente da tabela orcamento_api usando o id_orcamento.
        
        Args:
            db: Sessão do banco de dados para consultar orcamento_api
            id_orcamento: ID do orçamento a ser aprovado
            data_entrega: Data de entrega no formato ISO (ex: 2026-01-15T12:00:00.000-03:00)
            gerar_op: Se deve gerar OP automaticamente (default: True)
            
        Returns:
            Dict com resposta da API de aprovação
        """
        logger.info(f"Aprovando orçamento {id_orcamento} na API Bremen")
        
        try:
            # Buscar itens da tabela orcamento_api
            itens_db = db.query(OrcamentoAPI).filter(
                OrcamentoAPI.id_orcamento == id_orcamento
            ).all()
            
            if not itens_db:
                raise ValueError(f"Nenhum item encontrado na tabela orcamento_api para o orçamento {id_orcamento}")
            
            logger.info(f"Encontrados {len(itens_db)} itens na tabela orcamento_api para orçamento {id_orcamento}")
            
            # Montar lista de itens para aprovação usando id_item da tabela
            itens_aprovacao = []
            for item in itens_db:
                if item.id_item:
                    itens_aprovacao.append({
                        "id": item.id_item,
                        "data_entrega": data_entrega
                    })
                    logger.debug(f"Item adicionado para aprovação: id={item.id_item}")
            
            if not itens_aprovacao:
                raise ValueError(f"Nenhum item válido (id_item) encontrado para aprovação do orçamento {id_orcamento}")
            
            # Montar payload de aprovação
            payload = {
                "identifier": "PageFlow",
                "data": {
                    "id_orcamento": id_orcamento,
                    "gerar_op": gerar_op,
                    "itens": itens_aprovacao
                }
            }
            
            logger.info(f"Payload de aprovação montado com {len(itens_aprovacao)} itens")
            logger.debug(f"Payload de aprovação: {json.dumps(payload, indent=2)}")
            
            # Fazer requisição para API Bremen
            url = f"{self.api_base_url}/api/v1/proposta/aprovar"
            
            async with httpx.AsyncClient(timeout=self.api_timeout) as client:
                response = await client.post(
                    url=url,
                    headers=self.headers,
                    json=payload
                )
                
                response.raise_for_status()
                result = response.json()
                
                logger.info(f"Orçamento {id_orcamento} aprovado com sucesso")
                return result
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP ao aprovar orçamento: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Erro ao aprovar orçamento: {str(e)}")
            raise
    
    def extrair_itens_orcamento(self, resposta_api: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrai itens do orçamento da resposta da API
        
        Cada item extraído contém:
        - id: ID do item no orçamento (usado na aprovação)
        - descricao: Descrição do produto
        - quantidade: Quantidade do item
        
        Args:
            resposta_api: Resposta completa da API de geração de orçamento
            
        Returns:
            Lista de itens simplificada para persistência e aprovação
        """
        try:
            itens = []
            
            if 'data' in resposta_api and 'itens' in resposta_api['data']:
                for item in resposta_api['data']['itens']:
                    item_simplificado = {
                        'id': item.get('id'),
                        'id_produto': item.get('id_produto'),
                        'descricao': item.get('descricao', ''),
                        'quantidade': item.get('quantidade', 0)
                    }
                    # Remover campos None para manter JSON limpo
                    item_simplificado = {k: v for k, v in item_simplificado.items() if v is not None}
                    itens.append(item_simplificado)
                    
                logger.debug(f"Extraídos {len(itens)} itens do orçamento")
            else:
                logger.warning("Resposta da API não contém itens no formato esperado")
            
            return itens
            
        except Exception as e:
            logger.error(f"Erro ao extrair itens do orçamento: {str(e)}")
            return []
    
    def extrair_pedidos_aprovacao(self, resposta_api: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrai pedidos da resposta da API de aprovação
        
        Args:
            resposta_api: Resposta completa da API de aprovação
            
        Returns:
            Lista de pedidos gerados na aprovação
        """
        try:
            pedidos = []
            
            if 'data' in resposta_api:
                data = resposta_api['data']
                
                # Tentar extrair do campo 'pedidos'
                if 'pedidos' in data:
                    pedidos = data['pedidos']
                    logger.debug(f"Extraídos {len(pedidos)} pedidos do campo 'pedidos'")
                    
                # Se não houver pedidos, extrair dos itens
                elif 'itens' in data:
                    pedidos = data['itens']
                    logger.debug(f"Extraídos {len(pedidos)} itens como pedidos")
            
            return pedidos if isinstance(pedidos, list) else []
            
        except Exception as e:
            logger.error(f"Erro ao extrair pedidos da aprovação: {str(e)}")
            return []
