-- Query para buscar pedidos de uma escola em estrutura hierárquica (cascata)
-- Parâmetros: :escola_id, :tipo_formulario

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
        AND distri.status_id = 1
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
