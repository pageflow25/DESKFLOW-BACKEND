import httpx
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..models.orcamento_api import OrcamentoAPI
from ..models.distribuicao_material import DistribuicaoMaterial
from ..schemas.orcamento import OrcamentoResponse
from .bremen_client import BremenClient

logger = get_logger(__name__)
settings = get_settings()

REQUEST_DELAY_SECONDS = 2  # Delay entre requisições para não sobrecarregar API


class OrcamentoAPIService:
    """Service para integração com APIs externas de orçamento Bremen"""

    @staticmethod
    def _formatar_data_entrega_iso(data_raw: Any) -> str | None:
        """Converte data (YYYY-MM-DD) para formato ISO esperado pela API Bremen."""
        if data_raw is None:
            return None

        valor = str(data_raw).strip()
        if not valor:
            return None

        try:
            data_obj = datetime.fromisoformat(valor[:10])
            return data_obj.strftime("%Y-%m-%dT18:00:00.000-03:00")
        except Exception:
            return None
    
    def __init__(self):
        # URLs das APIs Bremen
        self.api_base_url = settings.BREMEN_API_URL
        self.api_timeout = settings.API_TIMEOUT
        self.retry_max_wait_seconds = max(0, settings.BREMEN_503_MAX_WAIT_SECONDS)
        self.retry_base_seconds = max(1, settings.BREMEN_503_RETRY_BASE_SECONDS)
        self.retry_max_interval_seconds = max(1, settings.BREMEN_503_RETRY_MAX_INTERVAL_SECONDS)
        
        # Cliente Bremen com renovação automática de token
        self.bremen_client = BremenClient()

    def _calcular_delay_retry(self, tentativa: int) -> int:
        """Calcula delay progressivo entre tentativas, limitado por configuração."""
        return min(self.retry_base_seconds * max(tentativa, 1), self.retry_max_interval_seconds)
    
    async def _fazer_requisicao_com_retry(
        self, 
        url: str, 
        payload: Dict[str, Any],
        operacao: str = "requisição"
    ) -> Dict[str, Any]:
        """
        Faz uma requisição POST com retry automático para erros 503.
        A renovação do token (401) é tratada automaticamente pelo BremenClient.
        
        Args:
            url: URL da API (caminho relativo, ex: /api/v1/orcamento)
            payload: Dados a enviar
            operacao: Nome da operação (para logs)
            
        Returns:
            Dict com resposta da API
        """
        # Extrair path relativo se URL completa foi fornecida
        path = url.replace(self.api_base_url, "") if url.startswith(self.api_base_url) else url
        
        last_error = None
        tentativa = 1
        loop = asyncio.get_running_loop()
        inicio = loop.time()
        prazo_final = inicio + self.retry_max_wait_seconds

        while True:
            try:
                logger.info(
                    f"Tentativa {tentativa} - {operacao} "
                    f"(janela retry 503: {self.retry_max_wait_seconds}s)"
                )
                
                # BremenClient cuida da autenticação e renovação do token
                response = await self.bremen_client.post(path, payload)
                
                # Se for 503, fazer retry
                if response.status_code == 503:
                    error_msg = response.text
                    agora = loop.time()
                    tempo_decorrido = int(agora - inicio)
                    tempo_restante = int(max(0, prazo_final - agora))

                    if agora >= prazo_final:
                        logger.error(
                            "API Bremen permaneceu em 503 até o limite de retry "
                            f"({self.retry_max_wait_seconds}s)."
                        )
                        response.raise_for_status()

                    delay = min(self._calcular_delay_retry(tentativa), max(1, tempo_restante))
                    logger.warning(
                        f"API retornou 503 (ocupada). Tentativa {tentativa}. "
                        f"Decorrido: {tempo_decorrido}s, restante: {tempo_restante}s. "
                        f"Aguardando {delay}s para nova tentativa..."
                    )
                    logger.debug(f"Resposta 503: {error_msg}")

                    await asyncio.sleep(delay)
                    tentativa += 1
                    continue
                
                response.raise_for_status()
                return response.json()
                    
            except httpx.ConnectError as e:
                last_error = e
                logger.error(f"ERRO DE CONEXÃO com API Bremen ({self.api_base_url}): {str(e)}")
                logger.error("Verifique: 1) Servidor Bremen online? 2) Rede/VPN conectada? 3) IP correto?")

                agora = loop.time()
                if agora >= prazo_final:
                    logger.error(
                        "Timeout de retry por erro de conexão atingido "
                        f"({self.retry_max_wait_seconds}s)."
                    )
                    raise

                tempo_restante = int(max(0, prazo_final - agora))
                delay = min(self._calcular_delay_retry(tentativa), max(1, tempo_restante))
                logger.info(
                    f"Aguardando {delay}s antes de tentar novamente "
                    f"(restante da janela: {tempo_restante}s)..."
                )
                await asyncio.sleep(delay)
                tentativa += 1
                continue

            except httpx.ReadTimeout as e:
                last_error = e
                logger.error(
                    f"Timeout de leitura na operação '{operacao}' após {self.api_timeout}s: {str(e)}"
                )

                agora = loop.time()
                if agora >= prazo_final:
                    raise

                tempo_restante = int(max(0, prazo_final - agora))
                delay = min(self._calcular_delay_retry(tentativa), max(1, tempo_restante))
                logger.info(
                    f"Aguardando {delay}s após timeout para nova tentativa "
                    f"(restante da janela: {tempo_restante}s)..."
                )
                await asyncio.sleep(delay)
                tentativa += 1
                continue
                
            except httpx.HTTPStatusError as e:
                last_error = e
                # Para outros erros HTTP que não sejam 503, não fazer retry
                if e.response.status_code != 503:
                    logger.error(f"Erro HTTP: {e.response.status_code} - {e.response.text}")
                    raise

                agora = loop.time()
                if agora >= prazo_final:
                    raise

                tempo_restante = int(max(0, prazo_final - agora))
                delay = min(self._calcular_delay_retry(tentativa), max(1, tempo_restante))
                await asyncio.sleep(delay)
                tentativa += 1
                continue
                
            except Exception as e:
                last_error = e
                logger.error(f"Erro ao fazer {operacao}: {str(e)}")

                agora = loop.time()
                if agora >= prazo_final:
                    raise

                tempo_restante = int(max(0, prazo_final - agora))
                delay = min(self._calcular_delay_retry(tentativa), max(1, tempo_restante))
                await asyncio.sleep(delay)
                tentativa += 1
                continue
        
        # Se chegou aqui, todas as tentativas falharam
        raise last_error if last_error else Exception("Todas as tentativas falharam")
    
    async def enviar_orcamento(self, orcamento: OrcamentoResponse) -> Dict[str, Any]:
        """
        Envia orçamento para a API Bremen (FASE 01)
        
        Retorna dict com:
        - resposta_api: Resposta completa da API
        - payload_enviado: Payload original enviado (contém id_distribuicao nos componentes)
        
        Args:
            orcamento: Dados do orçamento para envio
            
        Returns:
            Dict com { "resposta_api": {...}, "payload_enviado": {...} }
        """
        logger.info("Enviando orçamento para API Bremen")
        
        # Converter orcamento para formato da API
        payload = {
            "identifier": orcamento.identifier,
            "data": {
                "id_cliente": orcamento.data.id_cliente,
                "id_vendedor": orcamento.data.id_vendedor or 2285,
                "id_forma_pagamento": orcamento.data.id_forma_pagamento,
                "itens": [
                    {
                        "id_produto": item.id_produto,
                        "titulo": item.titulo or f"Produto {item.id_produto}",
                        "quantidade": item.quantidade,
                        "usar_listapreco": item.usar_listapreco,
                        "manter_estrutura_mod_produto": item.manter_estrutura_mod_produto,
                        # ids_distribuicao é mantido no payload para rastreio interno,
                        # mas NÃO será enviado para a API Bremen
                        "ids_distribuicao": item.ids_distribuicao,
                        "componentes": [
                            {
                                "id": comp.id,
                                "id_distribuicao": comp.id_distribuicao,
                                "descricao": comp.descricao,
                                "altura": comp.altura,
                                "largura": comp.largura,
                                "quantidade_paginas": comp.quantidade_paginas,
                                "idgruposubstratoimpressao": comp.idgruposubstratoimpressao,
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
        
        # Construir payload para envio à API Bremen (sem campos internos)
        payload_api = {
            "identifier": payload["identifier"],
            "data": {
                "id_cliente": payload["data"]["id_cliente"],
                "id_vendedor": payload["data"]["id_vendedor"],
                "id_forma_pagamento": payload["data"]["id_forma_pagamento"],
                "itens": [
                    {
                        k: v for k, v in item.items()
                        if k not in ("ids_distribuicao", "id_distribuicao")  # Remover campos internos
                    }
                    for item in payload["data"]["itens"]
                ]
            }
        }
        
        # Limpar id_distribuicao dos componentes para a API Bremen
        for item in payload_api["data"]["itens"]:
            item["componentes"] = [
                {k: v for k, v in comp.items() if k != "id_distribuicao"}
                for comp in item.get("componentes", [])
            ]
        
        url = f"{self.api_base_url}/api/v1/orcamento"
        logger.info(f"Conectando à API Bremen: {url}")
        logger.debug(f"Payload com {len(payload_api['data']['itens'])} itens")
        
        resposta_api = await self._fazer_requisicao_com_retry(url, payload_api, "enviar orçamento")
        
        id_orcamento = resposta_api.get('data', {}).get('id_orcamento')
        logger.info(f"Orçamento enviado com sucesso. ID: {id_orcamento}")
        
        # Retornar tanto a resposta da API quanto o payload enviado
        # O payload contém id_distribuicao nos componentes, essencial para a correspondência sequencial
        return {
            "resposta_api": resposta_api,
            "payload_enviado": payload
        }
    
    async def aprovar_orcamento(
        self, 
        db: Session,
        id_orcamento: int, 
        data_entrega: Optional[str],
        gerar_op: bool = True,
        usar_data_saida_distribuicao: bool = False,
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
        
        # Buscar itens da tabela orcamento_api
        itens_db = db.query(OrcamentoAPI).filter(
            OrcamentoAPI.id_orcamento == id_orcamento
        ).all()
        
        if not itens_db:
            raise ValueError(f"Nenhum item encontrado na tabela orcamento_api para o orçamento {id_orcamento}")
        
        logger.info(f"Encontrados {len(itens_db)} itens na tabela orcamento_api para orçamento {id_orcamento}")
        
        # Buscar data_saida por distribuição para aprovação por item (quando habilitado)
        datas_entrega_por_distribuicao: Dict[int, str] = {}
        if usar_data_saida_distribuicao:
            ids_dist = [item.distribuicao_material_id for item in itens_db if item.distribuicao_material_id is not None]
            if ids_dist:
                rows_dist = db.query(DistribuicaoMaterial.id, DistribuicaoMaterial.data_saida).filter(
                    DistribuicaoMaterial.id.in_(ids_dist)
                ).all()
                datas_entrega_por_distribuicao = {
                    row.id: self._formatar_data_entrega_iso(row.data_saida)
                    for row in rows_dist
                }

        # Montar lista de itens para aprovação usando id_item da tabela
        itens_aprovacao = []
        for item in itens_db:
            if item.id_item:
                data_item = data_entrega
                if usar_data_saida_distribuicao:
                    data_item = datas_entrega_por_distribuicao.get(item.distribuicao_material_id)

                    if not data_item and data_entrega:
                        data_item = data_entrega

                    if not data_item:
                        raise ValueError(
                            f"Distribuição {item.distribuicao_material_id} sem data_saida válida para aprovação do item {item.id_item}"
                        )

                itens_aprovacao.append({
                    "id": item.id_item,
                    "data_entrega": data_item
                })
                logger.debug(
                    f"Item adicionado para aprovação: id={item.id_item}, dist={item.distribuicao_material_id}, data_entrega={data_item}"
                )
        
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
        
        url = f"{self.api_base_url}/api/v1/proposta/aprovar"
        
        result = await self._fazer_requisicao_com_retry(url, payload, f"aprovar orçamento {id_orcamento}")
        
        logger.info(f"Orçamento {id_orcamento} aprovado com sucesso")
        return result
    
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
                
                # Se data for uma lista, retornar diretamente como pedidos
                if isinstance(data, list):
                    logger.debug(f"Campo 'data' é uma lista com {len(data)} itens, usando como pedidos")
                    return data
                
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


async def aguardar_entre_requisicoes():
    """Aguarda um tempo entre requisições para não sobrecarregar a API"""
    logger.debug(f"Aguardando {REQUEST_DELAY_SECONDS}s antes da próxima requisição...")
    await asyncio.sleep(REQUEST_DELAY_SECONDS)
