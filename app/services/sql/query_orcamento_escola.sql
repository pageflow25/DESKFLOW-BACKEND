-- Query para geração de orçamentos AGRUPADOS POR ESCOLA (soma quantidades de todas as unidades)
-- Diferença do modo "por unidade": aqui agrupa tudo da escola em um único orçamento por turma
-- Usa ids_distribuicao (array) em vez de id_distribuicao (único) nos itens
-- Parâmetros: :escola_id, :ids_produtos, :datas_saida, :divisoes_logistica, :dias_uteis_filtro, :ids_formularios, :status_ids, :ids_unidades, :ids_arquivos
--
-- Reescrita para a nova arquitetura de distribuição (migrations 20260831120000 e
-- 20260831130000 do PAGEFLOW). Ver comentário equivalente em query_orcamento.sql
-- para o detalhe do que mudou no schema.
--
-- Particularidade deste modo: um mesmo item de carrinho (pedido_item_carrinho_id)
-- pode estar distribuído em várias pedido_distribuicoes — uma por unidade/turma
-- de destino. Antes isso exigia somar por uma chave heurística (pares/especificação)
-- e filtrar só "miolo" pra não contar a quantidade em dobro (cada arquivo tinha sua
-- própria linha de distribuição). Agora cada pedido_distribuicoes já é 1 linha por
-- destino com 1 quantidade só, então basta SUM(quantidade) agrupando por
-- (pedido_item_carrinho_id, id_turma) — sem duplicação possível.

WITH parametros AS (
    SELECT
        :escola_id AS escola_id,
        CAST(:ids_produtos AS int[]) AS ids_produtos,
        CAST(:datas_saida AS date[]) AS datas_saida,
        CAST(:divisoes_logistica AS text[]) AS divisoes_logistica,
        CAST(:dias_uteis_filtro AS int[]) AS dias_uteis_filtro,
        CAST(:ids_formularios AS int[]) AS ids_formularios,
        CAST(:status_ids AS int[]) AS status_ids,
        CAST(:ids_unidades AS int[]) AS ids_unidades,
        CAST(:ids_arquivos AS int[]) AS ids_arquivos
),

unidades_filtradas AS (
    SELECT
        ue.id,
        ue.nome,
        ue.cliente_id,
        ue.forma_pagamento,
        ue.escola_id,
        ue.client_id_venda,
        ue.vendedor_id_venda
    FROM escola_unidades ue
    CROSS JOIN parametros p
    WHERE ue.escola_id = p.escola_id
    AND (p.divisoes_logistica IS NULL OR ue.divisao_logistica = ANY(p.divisoes_logistica))
    AND (p.dias_uteis_filtro IS NULL OR ue.dias_uteis = ANY(p.dias_uteis_filtro))
    AND (p.ids_unidades IS NULL OR ue.id = ANY(p.ids_unidades))
),

-- Cabeçalho: já é 1 linha por item comercial entregue a uma unidade/turma.
distribuicoes AS (
    SELECT
        dm.id AS distribuicao_id,
        uf.id AS unidade_id,
        uf.escola_id,
        dm.formulario_id,
        dm.pedido_item_carrinho_id,
        dm.quantidade,
        dm.id_turma
    FROM unidades_filtradas uf
    CROSS JOIN parametros p
    JOIN pedido_distribuicoes dm ON dm.unidade_escolar_id = uf.id
    WHERE dm.quantidade > 0
        AND dm.status_id = ANY(p.status_ids)
        AND (
            p.datas_saida IS NULL
            OR dm.data_saida::date = ANY(p.datas_saida)
            OR dm.data_saida IS NULL
        )
        AND (p.ids_formularios IS NULL OR dm.formulario_id = ANY(p.ids_formularios))
        AND (p.ids_produtos IS NULL OR EXISTS (
            SELECT 1
            FROM pedido_distribuicao_arquivos pda
            JOIN pedido_especificacoes ef ON ef.id = pda.especificacao_form_id
            WHERE pda.distribuicao_material_id = dm.id
              AND ef.id_produto = ANY(p.ids_produtos)
        ))
        AND (p.ids_arquivos IS NULL OR EXISTS (
            SELECT 1
            FROM pedido_distribuicao_arquivos pda
            WHERE pda.distribuicao_material_id = dm.id
              AND pda.arquivo_pdf_id = ANY(p.ids_arquivos)
        ))
),

-- Materiais (arquivos) de cada distribuição — 1 linha por pedido_distribuicao_arquivos,
-- já com o componente Bremen resolvido de forma determinística.
materiais AS (
    SELECT
        pda.distribuicao_material_id AS distribuicao_id,
        pda.arquivo_pdf_id,
        pda.especificacao_form_id,
        pda.id_componente,
        ef.id_produto,
        ef.corfrente,
        ef.corverso,
        ef.gramatura_miolo,
        ap.nome AS arquivo_nome,
        ap.paginas,
        bg.gramatura AS gramatura_catalogo,
        bt.idgruposubstratoimpressao,
        COALESCE(bt.altura, NULLIF(ef.altura, '')::numeric) AS altura_mm,
        COALESCE(bt.largura, NULLIF(ef.largura, '')::numeric) AS largura_mm,
        bi.descricao AS produto_descricao,
        bi.sub_grupo,
        bi.frente_verso,
        bi."categoria_Prod",
        bc.descricao AS componente_descricao,
        COALESCE(bc.is_capa, FALSE) AS is_capa,
        COALESCE(bc.is_miolo, FALSE) AS is_miolo
    FROM pedido_distribuicao_arquivos pda
    JOIN pedido_especificacoes ef ON ef.id = pda.especificacao_form_id
    JOIN pedido_arquivos_pdf ap ON ap.id = pda.arquivo_pdf_id
    JOIN bremen_itens bi ON bi.id_produto = ef.id_produto
    LEFT JOIN bremen_componentes bc ON bc.id_componente = pda.id_componente
    LEFT JOIN bremen_gramatura bg ON bg.id = ef.id_gramatura
    LEFT JOIN bremen_tamanho_papel bt ON bt.id = ef.id_papel
),

-- Soma as quantidades de todas as unidades da escola que compartilham o
-- mesmo item de carrinho (mesmo item comercial, distribuído em vários destinos).
itens_agrupados AS (
    SELECT
        d.pedido_item_carrinho_id,
        d.id_turma,
        MAX(d.formulario_id) AS formulario_id,
        MIN(d.distribuicao_id) AS distribuicao_referencia,
        SUM(d.quantidade) AS quantidade_total,
        -- jsonb (não json): precisa suportar comparação de igualdade pra poder
        -- entrar no GROUP BY do CTE seguinte (itens_produto).
        jsonb_agg(DISTINCT d.distribuicao_id) AS ids_distribuicao
    FROM distribuicoes d
    GROUP BY d.pedido_item_carrinho_id, d.id_turma
),

-- Agrega os metadados do item comercial a partir dos materiais da distribuição
-- de referência (todas as distribuições do grupo compartilham o mesmo item de
-- carrinho, logo os mesmos arquivos/especificações).
itens_produto AS (
    SELECT
        ia.pedido_item_carrinho_id,
        ia.id_turma,
        ia.formulario_id,
        ia.distribuicao_referencia,
        ia.quantidade_total,
        ia.ids_distribuicao,
        p.escola_id,
        t.nome AS nome_turma,
        t.area AS area_turma,
        MAX(mat.id_produto) AS id_produto,
        COALESCE(
            MAX(CASE WHEN mat.is_miolo THEN mat.especificacao_form_id END),
            MAX(mat.especificacao_form_id)
        ) AS especificacao_id_geral,
        COALESCE(
            CASE
                WHEN ia.id_turma IS NOT NULL AND NULLIF(TRIM(t.nome), '') IS NOT NULL THEN
                    '(#' || TRIM(t.nome) || ') - '
                    || UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
                        COALESCE(
                            MAX(CASE WHEN mat.is_miolo THEN mat.arquivo_nome END),
                            MAX(mat.arquivo_nome)
                        ), '\.pdf$', '', 'i'), '[_-]+', ' ', 'g')))
                    || ' - (#' || MAX(form.id) || ')'
                ELSE
                    UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
                        COALESCE(
                            MAX(CASE WHEN mat.is_miolo THEN mat.arquivo_nome END),
                            MAX(mat.arquivo_nome)
                        ), '\.pdf$', '', 'i'), '[_-]+', ' ', 'g')))
                    || ' (#' || MAX(form.id) || ')'
            END,
            'Produto ' || MAX(mat.id_produto)
        ) AS nome_arquivo,
        MAX(mat.altura_mm) AS altura,
        MAX(mat.largura_mm) AS largura,
        MAX(mat.gramatura_miolo) AS gramatura_miolo,
        MAX(form.observacoes) AS obs_producao,
        MAX(TO_CHAR(form.data_entrega, 'DD/MM/YYYY')) AS data_entrega_pedido,
        MAX(form.titulo) AS form_titulo,
        CASE
            WHEN (MAX(mat.paginas) > 2 AND UPPER(MAX(mat.frente_verso)) = 'FV' AND UPPER(MAX(mat."categoria_Prod")) = 'PROVA')
              OR (MAX(mat.paginas) > 1 AND UPPER(MAX(mat.frente_verso)) = 'SF' AND UPPER(MAX(mat."categoria_Prod")) = 'PROVA')
            THEN 'normal'
            ELSE 'separado'
        END AS tipo_agrupamento
    FROM itens_agrupados ia
    CROSS JOIN parametros p
    LEFT JOIN escola_turmas t ON t.id = ia.id_turma
    JOIN materiais mat ON mat.distribuicao_id = ia.distribuicao_referencia
    JOIN pedido_formularios form ON form.id = ia.formulario_id
    GROUP BY
        ia.pedido_item_carrinho_id, ia.id_turma, ia.formulario_id, ia.distribuicao_referencia,
        ia.quantidade_total, ia.ids_distribuicao, p.escola_id, t.nome, t.area
),

respostas_componentes AS (
    SELECT DISTINCT ON (mat.distribuicao_id, mat.id_componente, bp.id)
        mat.distribuicao_id,
        mat.especificacao_form_id,
        mat.id_componente,
        bp.id AS pergunta_id,
        br.descricao_opcao AS resposta
    FROM materiais mat
    JOIN bremen_perguntas bp ON bp.id_componente = mat.id_componente
    LEFT JOIN pedido_pergunta_resposta pr
        ON pr.pergunta_id = bp.id
        AND pr.especificacao_id = mat.especificacao_form_id
    LEFT JOIN bremen_respostas br ON br.id = pr.resposta_id
    WHERE br.valor IS NOT NULL
    ORDER BY mat.distribuicao_id, mat.id_componente, bp.id
),

respostas_gerais AS (
    SELECT DISTINCT ON (ip.distribuicao_referencia, bp.id)
        ip.distribuicao_referencia,
        bp.id AS pergunta_id,
        br.descricao_opcao AS resposta
    FROM itens_produto ip
    JOIN bremen_perguntas bp ON bp.id_geral = ip.id_produto
    LEFT JOIN pedido_pergunta_resposta pr
        ON pr.pergunta_id = bp.id
        AND pr.especificacao_id = ip.especificacao_id_geral
    LEFT JOIN bremen_respostas br ON br.id = pr.resposta_id
    WHERE br.valor IS NOT NULL
    ORDER BY ip.distribuicao_referencia, bp.id
),

-- Tarefas de escopo componente vinculadas à especificação via tabela pivot
tarefas_componentes AS (
    SELECT DISTINCT
        bet.especificacao_id,
        bt.id_tarefa,
        bt.descricao,
        bt.descricao_pf
    FROM pedido_especificacoes_tarefas bet
    JOIN bremen_tarefas bt ON bt.id = bet.tarefa_id
    WHERE bt.id_componente IS TRUE
),

-- Tarefas de escopo geral vinculadas à especificação via tabela pivot
tarefas_gerais AS (
    SELECT DISTINCT
        bet.especificacao_id,
        bt.id_tarefa,
        bt.descricao,
        bt.descricao_pf
    FROM pedido_especificacoes_tarefas bet
    JOIN bremen_tarefas bt ON bt.id = bet.tarefa_id
    WHERE bt.id_geral IS TRUE
)

SELECT json_strip_nulls(json_build_object(
    'identifier', 'PageFlow',
    'data', json_build_object(
        'id_escola', ip.escola_id,
        'id_cliente', (SELECT uf.client_id_venda FROM unidades_filtradas uf WHERE uf.escola_id = ip.escola_id LIMIT 1),
        'id_vendedor', (SELECT uf.vendedor_id_venda FROM unidades_filtradas uf WHERE uf.escola_id = ip.escola_id LIMIT 1),
        'id_forma_pagamento', (SELECT uf.forma_pagamento FROM unidades_filtradas uf WHERE uf.escola_id = ip.escola_id LIMIT 1),
        'itens', COALESCE(
            json_agg(
                json_build_object(
                    'id_produto', ip.id_produto,
                    'titulo', ip.nome_arquivo,
                    'obs_producao', CONCAT_WS(
                        CHR(10) || CHR(10),
                        ip.obs_producao,
                        CONCAT_WS(
                            CHR(10),
                            'Data de Entrega: ' || COALESCE(ip.data_entrega_pedido, '-'),
                            'Título: ' || COALESCE(ip.form_titulo, '-')
                        )
                    ),
                    'quantidade', ip.quantidade_total,
                    'usar_listapreco', 1,
                    'manter_estrutura_mod_produto', 1,
                    'ids_distribuicao', ip.ids_distribuicao,
                    'componentes', COALESCE((
                        SELECT json_agg(
                            CASE
                                -- ==========================================================
                                -- CENÁRIO 1: MIOLO
                                -- ==========================================================
                                WHEN mat.is_miolo THEN
                                    json_strip_nulls(json_build_object(
                                        'id', mat.id_componente,
                                        'descricao', mat.componente_descricao,
                                        'altura', ROUND(mat.altura_mm::numeric / 10, 2),
                                        'largura', ROUND(mat.largura_mm::numeric / 10, 2),
                                        'quantidade_paginas', COALESCE(mat.paginas, 0),
                                        'idgruposubstratoimpressao', mat.idgruposubstratoimpressao,
                                        'gramaturasubstratoimpressao', COALESCE(
                                            mat.gramatura_catalogo,
                                            NULLIF(replace(regexp_replace(mat.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric
                                        ),
                                        'corfrente', mat.corfrente,
                                        'corverso', mat.corverso,
                                        'perguntas_componente', COALESCE((
                                            SELECT json_agg(
                                                json_build_object(
                                                    'id_pergunta', bp.id_pergunta,
                                                    'pergunta', bp.nome,
                                                    'tipo', bp.tipo,
                                                    'resposta', rc.resposta
                                                )
                                                ORDER BY bp.id_pergunta
                                            )
                                            FROM bremen_perguntas bp
                                            INNER JOIN respostas_componentes rc
                                                ON rc.pergunta_id = bp.id
                                                AND rc.distribuicao_id = mat.distribuicao_id
                                                AND rc.id_componente = mat.id_componente
                                            WHERE bp.id_componente = mat.id_componente
                                        ), '[]'::json),
                                        'tarefas_componente', COALESCE((
                                            SELECT json_agg(
                                                json_build_object(
                                                    'id', tc.id_tarefa,
                                                    'descricao', tc.descricao
                                                )
                                                ORDER BY tc.id_tarefa
                                            )
                                            FROM tarefas_componentes tc
                                            WHERE tc.especificacao_id = mat.especificacao_form_id
                                        ), '[]'::json)
                                    ))

                                -- ==========================================================
                                -- CENÁRIO 2: CAPA
                                -- ==========================================================
                                WHEN mat.is_capa THEN
                                    json_strip_nulls(
                                        json_build_object(
                                            'id', mat.id_componente,
                                            'descricao', mat.componente_descricao,
                                            'altura', ROUND(mat.altura_mm::numeric / 10, 2),
                                            'largura', ROUND(mat.largura_mm::numeric / 10, 2),
                                            'quantidade_paginas', mat.paginas,
                                            'idgruposubstratoimpressao',
                                                CASE
                                                    WHEN UPPER(mat."categoria_Prod") = 'LIVRETO'
                                                         AND EXISTS (
                                                             SELECT 1 FROM materiais c_miolo
                                                             WHERE c_miolo.distribuicao_id = mat.distribuicao_id
                                                               AND c_miolo.is_miolo
                                                         )
                                                    THEN mat.idgruposubstratoimpressao
                                                    ELSE NULL
                                                END,
                                            'gramaturasubstratoimpressao',
                                                CASE
                                                    WHEN UPPER(mat."categoria_Prod") = 'LIVRETO'
                                                         AND EXISTS (
                                                             SELECT 1 FROM materiais c_miolo
                                                             WHERE c_miolo.distribuicao_id = mat.distribuicao_id
                                                               AND c_miolo.is_miolo
                                                         )
                                                    THEN
                                                        COALESCE(
                                                            mat.gramatura_catalogo,
                                                            NULLIF(replace(regexp_replace(mat.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric
                                                        )
                                                    ELSE NULL
                                                END,
                                            'corfrente', 4,
                                            'corverso', 0,
                                            'perguntas_componente', COALESCE((
                                                SELECT json_agg(
                                                    json_build_object(
                                                        'id_pergunta', bp.id_pergunta,
                                                        'pergunta', bp.nome,
                                                        'tipo', bp.tipo,
                                                        'resposta', rc.resposta
                                                    )
                                                    ORDER BY bp.id_pergunta
                                                )
                                                FROM bremen_perguntas bp
                                                INNER JOIN respostas_componentes rc
                                                    ON rc.pergunta_id = bp.id
                                                    AND rc.distribuicao_id = mat.distribuicao_id
                                                    AND rc.id_componente = mat.id_componente
                                                WHERE bp.id_componente = mat.id_componente
                                            ), '[]'::json),
                                            'tarefas_componente', COALESCE((
                                                SELECT json_agg(
                                                    json_build_object(
                                                        'id', tc.id_tarefa,
                                                        'descricao', tc.descricao
                                                    )
                                                    ORDER BY tc.id_tarefa
                                                )
                                                FROM tarefas_componentes tc
                                                WHERE tc.especificacao_id = mat.especificacao_form_id
                                            ), '[]'::json)
                                        )
                                    )

                                -- ==========================================================
                                -- CENÁRIO 3: OUTROS
                                -- ==========================================================
                                ELSE
                                    json_build_object(
                                        'id', mat.id_componente,
                                        'descricao', mat.componente_descricao,
                                        'altura', ROUND(mat.altura_mm::numeric / 10, 2),
                                        'largura', ROUND(mat.largura_mm::numeric / 10, 2),
                                        'perguntas_componente', COALESCE((
                                            SELECT json_agg(
                                                json_build_object(
                                                    'id_pergunta', bp.id_pergunta,
                                                    'pergunta', bp.nome,
                                                    'tipo', bp.tipo,
                                                    'resposta', rc.resposta
                                                )
                                                ORDER BY bp.id_pergunta
                                            )
                                            FROM bremen_perguntas bp
                                            INNER JOIN respostas_componentes rc
                                                ON rc.pergunta_id = bp.id
                                                AND rc.distribuicao_id = mat.distribuicao_id
                                                AND rc.id_componente = mat.id_componente
                                            WHERE bp.id_componente = mat.id_componente
                                        ), '[]'::json),
                                        'tarefas_componente', COALESCE((
                                            SELECT json_agg(
                                                json_build_object(
                                                    'id', tc.id_tarefa,
                                                    'descricao', tc.descricao
                                                )
                                                ORDER BY tc.id_tarefa
                                            )
                                            FROM tarefas_componentes tc
                                            WHERE tc.especificacao_id = mat.especificacao_form_id
                                        ), '[]'::json)
                                    )
                            END
                        )
                        FROM materiais mat
                        WHERE mat.distribuicao_id = ip.distribuicao_referencia
                    ), '[]'::json),
                    'perguntas_gerais', COALESCE((
                        SELECT
                            json_agg(
                                json_build_object(
                                    'tipo', bp.tipo,
                                    'pergunta', bp.nome,
                                    'resposta', rg.resposta,
                                    'id_pergunta', bp.id_pergunta
                                )
                            )
                        FROM bremen_perguntas bp
                        INNER JOIN respostas_gerais rg
                            ON rg.pergunta_id = bp.id
                           AND rg.distribuicao_referencia = ip.distribuicao_referencia
                        WHERE bp.id_geral = ip.id_produto
                    ), '[]'::json),
                    'tarefas_gerais', COALESCE((
                        SELECT json_agg(
                            json_build_object(
                                'id_tarefa', tg.id_tarefa,
                                'descricao', tg.descricao,
                                'descricao_pf', tg.descricao_pf
                            )
                            ORDER BY tg.id_tarefa
                        )
                        FROM tarefas_gerais tg
                        WHERE tg.especificacao_id = ip.especificacao_id_geral
                    ), '[]'::json)
                )
                ORDER BY ip.distribuicao_referencia
            ), '[]'::json
        )
    )
))
FROM itens_produto ip
GROUP BY ip.escola_id, ip.id_turma, ip.nome_turma, ip.tipo_agrupamento
ORDER BY ip.escola_id, ip.id_turma NULLS FIRST, ip.tipo_agrupamento DESC;
