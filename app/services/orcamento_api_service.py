"""
Service para integração com API externa de orçamento (Bremen)
"""

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List, Optional
from datetime import datetime
from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..models.orcamento_api import OrcamentoAPI

logger = get_logger(__name__)

# Carregar configurações centralizadas
settings = get_settings()


def _get_headers() -> Dict[str, str]:
    """Retorna headers para requisições à API Bremen"""
    return {
        "Accept": "*/*",
        "Authorization": settings.BREMEN_API_TOKEN,
        "Content-Type": "application/json",
    }


class OrcamentoAPIService:
    """Service para integração com API externa de orçamento"""

    @staticmethod
    def enviar_orcamento(payload: Dict[str, Any], timeout: int = 250) -> Dict[str, Any]:
        """
        Envia orçamento para a API externa Bremen
        
        Args:
            payload: Dados do orçamento no formato esperado pela API
            timeout: Timeout da requisição em segundos
            
        Returns:
            Resposta da API em formato dict
        """
        url = f"{settings.BREMEN_API_URL}/api/v1/orcamento"
        logger.info(f"Enviando orçamento para API: {url}")
        
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=_get_headers(), json=payload)
                response.raise_for_status()
                result = response.json()
                logger.info(f"Resposta da API: status={response.status_code}")
                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP ao enviar orçamento: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Erro ao enviar orçamento para API: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def aprovar_proposta(
        id_orcamento: int, 
        itens: List[Dict[str, Any]], 
        gerar_op: bool = True,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Aprova uma proposta na API externa Bremen
        
        Args:
            id_orcamento: ID do orçamento retornado pela API
            itens: Lista de itens com id e data_entrega
            gerar_op: Se deve gerar OP automaticamente
            timeout: Timeout da requisição em segundos
            
        Returns:
            Resposta da API em formato dict
        """
        url = f"{settings.BREMEN_API_URL}/api/v1/proposta/aprovar"
        logger.info(f"Aprovando proposta {id_orcamento} na API: {url}")
        
        payload = {
            "identifier": "PageFlow",
            "data": {
                "gerar_op": gerar_op,
                "id_orcamento": id_orcamento,
                "itens": itens
            }
        }
        
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=_get_headers(), json=payload)
                response.raise_for_status()
                result = response.json()
                logger.info(f"Proposta {id_orcamento} aprovada com sucesso")
                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Erro HTTP ao aprovar proposta: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Erro ao aprovar proposta: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def salvar_resposta_db(
        db: Session,
        distribuicao_material_id: int,
        id_orcamento: int,
        itens: List[Dict[str, Any]],
        resposta_api: Dict[str, Any]
    ) -> OrcamentoAPI:
        """
        Salva a resposta da API no banco de dados
        
        Args:
            db: Sessão do banco de dados
            distribuicao_material_id: ID da distribuição de material
            id_orcamento: ID do orçamento retornado pela API
            itens: Lista de itens do orçamento
            resposta_api: Resposta completa da API
            
        Returns:
            Registro criado no banco
        """
        logger.info(f"Salvando resposta da API para distribuicao_material_id={distribuicao_material_id}")
        
        try:
            orcamento_api = OrcamentoAPI(
                distribuicao_material_id=distribuicao_material_id,
                id_orcamento=id_orcamento,
                itens=itens,
                resposta_api=resposta_api
            )
            db.add(orcamento_api)
            db.commit()
            db.refresh(orcamento_api)
            
            logger.info(f"Registro salvo: OrcamentoAPI.id={orcamento_api.id}")
            return orcamento_api
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar resposta no banco: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def processar_orcamentos(
        db: Session,
        orcamentos: List[Dict[str, Any]],
        aprovar_automaticamente: bool = False,
        data_entrega: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processa uma lista de orçamentos: envia para API e salva no banco
        
        Args:
            db: Sessão do banco de dados
            orcamentos: Lista de orçamentos gerados pela query
            aprovar_automaticamente: Se deve aprovar a proposta automaticamente
            data_entrega: Data de entrega para aprovação (formato ISO)
            
        Returns:
            Resumo do processamento
        """
        resultados = {
            "total": len(orcamentos),
            "enviados": 0,
            "aprovados": 0,
            "salvos": 0,
            "erros": [],
            "detalhes": []
        }
        
        for idx, orcamento in enumerate(orcamentos):
            try:
                # Extrair mapeamento de id_distribuicao para cada item
                itens_original = orcamento.get("data", {}).get("itens", [])
                mapeamento_distribuicao = {}
                
                # Extrair _id_distribuicao e remover do payload
                itens_para_enviar = []
                for i, item in enumerate(itens_original):
                    id_dist = item.pop("_id_distribuicao", None)
                    if id_dist:
                        mapeamento_distribuicao[i] = id_dist
                    itens_para_enviar.append(item)
                
                # Atualizar payload sem _id_distribuicao
                payload = {
                    "identifier": orcamento.get("identifier", "PageFlow"),
                    "data": {
                        **orcamento.get("data", {}),
                        "itens": itens_para_enviar
                    }
                }
                
                # Enviar para API
                resposta = OrcamentoAPIService.enviar_orcamento(payload)
                resultados["enviados"] += 1
                
                id_orcamento = resposta.get("data", {}).get("id_orcamento")
                itens_resposta = resposta.get("data", {}).get("itens", [])
                
                # Mapear respostas com distribuicao_material_id
                for i, item_resp in enumerate(itens_resposta):
                    distribuicao_id = mapeamento_distribuicao.get(i)
                    if distribuicao_id:
                        try:
                            OrcamentoAPIService.salvar_resposta_db(
                                db=db,
                                distribuicao_material_id=distribuicao_id,
                                id_orcamento=id_orcamento,
                                itens=[item_resp],
                                resposta_api=resposta
                            )
                            resultados["salvos"] += 1
                        except Exception as e:
                            resultados["erros"].append(f"Erro ao salvar item {i}: {str(e)}")
                
                # Aprovar automaticamente se solicitado
                if aprovar_automaticamente and id_orcamento and itens_resposta:
                    data_entrega_final = data_entrega or datetime.now().strftime("%Y-%m-%dT12:00:00.000-03:00")
                    itens_aprovacao = [
                        {"id": item["id"], "data_entrega": data_entrega_final}
                        for item in itens_resposta
                    ]
                    OrcamentoAPIService.aprovar_proposta(id_orcamento, itens_aprovacao)
                    resultados["aprovados"] += 1
                
                resultados["detalhes"].append({
                    "indice": idx,
                    "id_orcamento": id_orcamento,
                    "itens_enviados": len(itens_para_enviar),
                    "itens_resposta": len(itens_resposta),
                    "status": "sucesso"
                })
                
            except Exception as e:
                logger.error(f"Erro ao processar orçamento {idx}: {str(e)}", exc_info=True)
                resultados["erros"].append(f"Orçamento {idx}: {str(e)}")
                resultados["detalhes"].append({
                    "indice": idx,
                    "status": "erro",
                    "mensagem": str(e)
                })
        
        logger.info(f"Processamento concluído: {resultados['enviados']}/{resultados['total']} enviados")
        return resultados
