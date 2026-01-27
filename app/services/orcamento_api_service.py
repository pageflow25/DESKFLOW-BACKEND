import httpx
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..schemas.orcamento import OrcamentoResponse, OrcamentoAPIResponse, ItemAPIResponse, OrcamentoAPIData

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
    
    async def aprovar_orcamento(self, id_orcamento: int, itens: List[Dict[str, Any]], 
                               data_entrega: str) -> Dict[str, Any]:
        """
        Aprova orçamento na API Bremen (FASE 02)
        
        Args:
            id_orcamento: ID do orçamento a ser aprovado
            itens: Lista de itens do orçamento
            data_entrega: Data de entrega no formato ISO
            
        Returns:
            Dict com resposta da API de aprovação
        """
        logger.info(f"Aprovando orçamento {id_orcamento} na API Bremen")
        
        try:
            # Montar payload de aprovação
            payload = {
                "identifier": "PageFlow",
                "data": {
                    "id_orcamento": id_orcamento,
                    "gerar_op": True,  # Fixo para COM DISTRIBUIÇÃO
                    "itens": [
                        {
                            "id": item["id"],
                            "data_entrega": data_entrega
                        }
                        for item in itens
                    ]
                }
            }
            
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
    
    def formatar_data_entrega(self, data: date, hora: str = "12:00:00") -> str:
        """
        Formata data de entrega para o formato ISO esperado pela API
        
        Args:
            data: Data de entrega
            hora: Hora no formato HH:MM:SS
            
        Returns:
            String da data no formato ISO com timezone
        """
        try:
            data_str = f"{data.isoformat()}T{hora}.000-03:00"
            return data_str
        except Exception as e:
            logger.error(f"Erro ao formatar data de entrega: {str(e)}")
            raise
    
    def extrair_itens_orcamento(self, resposta_api: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrai itens do orçamento da resposta da API
        
        Args:
            resposta_api: Resposta completa da API
            
        Returns:
            Lista de itens simplificada
        """
        try:
            if 'data' in resposta_api and 'itens' in resposta_api['data']:
                itens = []
                for item in resposta_api['data']['itens']:
                    item_simplificado = {
                        'id': item.get('id'),
                        'descricao': item.get('descricao', ''),
                        'quantidade': item.get('quantidade', 0)
                    }
                    itens.append(item_simplificado)
                return itens
            
            return []
            
        except Exception as e:
            logger.error(f"Erro ao extrair itens do orçamento: {str(e)}")
            return []
    
    def extrair_pedidos_aprovacao(self, resposta_api: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrai pedidos da resposta da API de aprovação
        
        Args:
            resposta_api: Resposta completa da API de aprovação
            
        Returns:
            Lista de pedidos
        """
        try:
            if 'data' in resposta_api and 'pedidos' in resposta_api['data']:
                return resposta_api['data']['pedidos']
            
            # Se não houver campo pedidos, extrair dos itens
            if 'data' in resposta_api and 'itens' in resposta_api['data']:
                return resposta_api['data']['itens']
            
            return []
            
        except Exception as e:
            logger.error(f"Erro ao extrair pedidos da aprovação: {str(e)}")
            return []
