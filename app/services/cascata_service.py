from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Dict, Any, List
import json
from ..config.logging_config import get_logger

logger = get_logger(__name__)


class CascataService:
    """Service para operações de pedidos em cascata"""

    @staticmethod
    def get_pedidos_escola_cascata(db: Session, escola_id: int, tipo_formulario: str = 'MEMOREX') -> List[Dict[str, Any]]:
        """
        Busca detalhes dos pedidos de uma escola em estrutura hierárquica
        
        Args:
            db: Sessão do banco de dados
            escola_id: ID da escola
            tipo_formulario: Tipo de formulário a filtrar (padrão: 'MEMOREX')
            
        Returns:
            Lista de divisões logísticas com produtos, datas e arquivos
        """
        logger.info(f"Buscando pedidos em cascata para escola_id={escola_id}, tipo_formulario={tipo_formulario}")
        
        # Query complexa com CTEs para estruturar dados hierarquicamente
        query = text("""
            WITH dados_normalizados AS (
                SELECT
                    -- TRATAMENTO DE NULOS
                    COALESCE(uc.divisao_logistica, 'Sem divisão') AS divisao_logistica,
                    COALESCE(CAST(uc.dias_uteis AS TEXT), 'Sem dias uteis') AS dias_uteis,

                    -- PRODUTO
                    b.id_produto,
                    b.descricao AS nome_produto,

                    -- DATA DE SAÍDA
                    COALESCE(
                        TO_CHAR(CAST(distri.data_saida AS DATE), 'YYYY-MM-DD'),
                        'Sem data saida'
                    ) AS data_saida_formatada,

                    -- ARQUIVO (NÍVEL 4)
                    ar.id AS arquivo_id,
                    ar.nome as nome_arquivo,
                    distri.quantidade as quantidade,
                    ar.paginas as paginas

                FROM formularios f
                INNER JOIN especificacoes_form e 
                    ON f.id = e.formulario_id
                INNER JOIN arquivo_pdfs ar 
                    ON ar.item_pedido_id = e.id
                INNER JOIN distribuicao_materiais distri 
                    ON distri.arquivo_pdf_id = ar.id
                INNER JOIN unidades_escolares uc 
                    ON distri.unidade_escolar_id = uc.id
                INNER JOIN bremen_itens b 
                    ON e.id_produto = b.id_produto
                WHERE UPPER(f.tipo_formulario) = UPPER(:tipo_formulario)
                    AND uc.escola_id = :escola_id
            ),

            -- NÍVEL 4: ARQUIVOS
            nivel_arquivos AS (
                SELECT
                    divisao_logistica,
                    dias_uteis,
                    id_produto,
                    nome_produto,
                    data_saida_formatada,

                    COUNT(*) AS qtd_arquivos,

                    JSONB_AGG(
                        JSONB_BUILD_OBJECT(
                            'arquivo', nome_arquivo,
                            'copias', quantidade,
                            'paginas', paginas
                        ) ORDER BY nome_arquivo ASC
                    ) AS lista_arquivos

                FROM dados_normalizados
                GROUP BY 1,2,3,4,5
            ),

            -- NÍVEL 3: DATAS
            nivel_datas AS (
                SELECT
                    divisao_logistica,
                    dias_uteis,
                    id_produto,
                    nome_produto,

                    SUM(qtd_arquivos) AS qtd_produto,

                    JSONB_AGG(
                        JSONB_BUILD_OBJECT(
                            'data_saida', data_saida_formatada,
                            'quantidade', qtd_arquivos,
                            'arquivos', lista_arquivos
                        ) ORDER BY data_saida_formatada DESC
                    ) AS lista_datas

                FROM nivel_arquivos
                GROUP BY 1,2,3,4
            ),

            -- NÍVEL 2: PRODUTOS
            nivel_produtos AS (
                SELECT
                    divisao_logistica,
                    dias_uteis,

                    SUM(qtd_produto) AS qtd_divisao,

                    JSONB_AGG(
                        JSONB_BUILD_OBJECT(
                            'id_produto', id_produto,
                            'produto', nome_produto,
                            'quantidade', qtd_produto,
                            'datas', lista_datas
                        ) ORDER BY nome_produto ASC
                    ) AS lista_produtos

                FROM nivel_datas
                GROUP BY 1,2
            ),

            -- NÍVEL 1: DIVISÕES
            nivel_divisoes AS (
                SELECT
                    JSONB_BUILD_OBJECT(
                        'divisao_logistica', divisao_logistica,
                        'dias_uteis', dias_uteis,
                        'quantidade_total', qtd_divisao,
                        'produtos', lista_produtos
                    ) AS objeto_divisao
                FROM nivel_produtos
                ORDER BY divisao_logistica ASC
            )

            -- RESULTADO FINAL
            SELECT 
                JSONB_AGG(objeto_divisao) AS dashboard_completo
            FROM nivel_divisoes;
        """)
        
        try:
            result = db.execute(query, {"escola_id": escola_id, "tipo_formulario": tipo_formulario})
            row = result.fetchone()
            
            if row and row.dashboard_completo:
                # Converter JSONB para dict Python
                dashboard_data = row.dashboard_completo
                logger.info(f"Dados em cascata obtidos com sucesso para escola_id={escola_id}")
                return dashboard_data
            else:
                logger.warning(f"Nenhum dado encontrado para escola_id={escola_id}")
                return []
                
        except Exception as e:
            logger.error(f"Erro ao buscar pedidos em cascata para escola_id={escola_id}: {str(e)}", exc_info=True)
            raise
