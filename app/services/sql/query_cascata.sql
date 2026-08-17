-- Query para buscar pedidos de uma escola em estrutura hierárquica (cascata)
-- Parâmetros: :escola_id, :tipo_formulario, :ids_formularios, :status_ids, :nome_arquivo_filtro

WITH parametros AS (
    SELECT
        :escola_id AS escola_id,
        NULLIF(TRIM(CAST(:tipo_formulario AS TEXT)), '') AS tipo_formulario,
        CAST(:ids_formularios AS int[]) AS ids_formularios,
    CAST(:status_ids AS int[]) AS status_ids,
    NULLIF(TRIM(CAST(:nome_arquivo_filtro AS TEXT)), '') AS nome_arquivo_filtro
),

dados_normalizados AS (
    SELECT
        -- TRATAMENTO DE NULOS
        COALESCE(uc.divisao_logistica, 'Sem divisão') AS divisao_logistica,
        COALESCE(CAST(uc.dias_uteis AS TEXT), 'Sem dias uteis') AS dias_uteis,

        -- PRODUTO
        COALESCE(b.id_produto, e.id_produto) AS id_produto,
        COALESCE(b.descricao, CONCAT('Produto ', COALESCE(CAST(e.id_produto AS TEXT), 'N/A'))) AS nome_produto,

        -- DATA DE SAÍDA
        COALESCE(
            TO_CHAR(distri.data_saida::DATE, 'YYYY-MM-DD'),
            'Sem data saida'
        ) AS data_saida_formatada,

        -- UNIDADE ESCOLAR
        uc.id AS unidade_id,
        uc.nome AS nome_unidade,

        -- ARQUIVO
        ar.id AS arquivo_id,
        COALESCE(ar.nome, 'Sem arquivo vinculado') AS nome_arquivo,
        distri.quantidade as quantidade,
        ar.paginas as paginas

    FROM pedido_formularios f
    CROSS JOIN parametros p
    INNER JOIN pedido_especificacoes e 
        ON f.id = e.formulario_id
    INNER JOIN pedido_distribuicoes distri 
        ON distri.especificacao_form_id = e.id
    INNER JOIN escola_unidades uc
        ON distri.unidade_escolar_id = uc.id
    INNER JOIN escola_escolas esc
        ON esc.id = uc.escola_id
    LEFT JOIN pedido_arquivos_pdf ar
        ON ar.id = distri.arquivo_pdf_id
    LEFT JOIN bremen_itens b 
        ON e.id_produto = b.id_produto
    WHERE (
            p.tipo_formulario IS NULL
            OR UPPER(f.tipo_formulario) = UPPER(p.tipo_formulario)
        )
        AND uc.escola_id = p.escola_id
        AND (p.status_ids IS NULL OR distri.status_id = ANY(p.status_ids))
        AND (p.ids_formularios IS NULL OR f.id = ANY(p.ids_formularios))
        AND (
            p.nome_arquivo_filtro IS NULL
            OR UPPER(COALESCE(ar.nome, '')) LIKE '%' || UPPER(p.nome_arquivo_filtro) || '%'
        )
        AND NOW() >= LEAST(
                f.criado_em + INTERVAL '30 minutes',
                CASE
                    WHEN CAST(esc.tipo_escola AS TEXT) = 'conveniado'
                         AND f.criado_em < date_trunc('day', f.criado_em) + INTERVAL '16 hours'
                    THEN date_trunc('day', f.criado_em) + INTERVAL '16 hours'
                    ELSE f.criado_em + INTERVAL '30 minutes'
                END
            )
),

-- NÍVEL 5: ARQUIVOS (agrupados por divisão + produto + data + unidade)
nivel_arquivos AS (
    SELECT
        divisao_logistica,
        dias_uteis,
        id_produto,
        nome_produto,
        data_saida_formatada,
        unidade_id,
        nome_unidade,

        COUNT(*) AS qtd_arquivos,

        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'id', arquivo_id,
                'arquivo', nome_arquivo,
                'copias', quantidade,
                'paginas', paginas
            ) ORDER BY nome_arquivo ASC
        ) AS lista_arquivos

    FROM dados_normalizados
    GROUP BY 1,2,3,4,5,6,7
),

-- NÍVEL 4: UNIDADES ESCOLARES
nivel_unidades AS (
    SELECT
        divisao_logistica,
        dias_uteis,
        id_produto,
        nome_produto,
        data_saida_formatada,

        SUM(qtd_arquivos) AS qtd_data,

        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'id', unidade_id,
                'unidade', nome_unidade,
                'quantidade', qtd_arquivos,
                'arquivos', lista_arquivos
            ) ORDER BY nome_unidade ASC
        ) AS lista_unidades

    FROM nivel_arquivos
    GROUP BY 1,2,3,4,5
),

-- NÍVEL 3: DATAS
nivel_datas AS (
    SELECT
        divisao_logistica,
        dias_uteis,
        id_produto,
        nome_produto,

        SUM(qtd_data) AS qtd_produto,

        JSONB_AGG(
            JSONB_BUILD_OBJECT(
                'data_saida', data_saida_formatada,
                'quantidade', qtd_data,
                'unidades', lista_unidades
            ) ORDER BY data_saida_formatada DESC
        ) AS lista_datas

    FROM nivel_unidades
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