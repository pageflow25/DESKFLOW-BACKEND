-- Query para geração de orçamentos AGRUPADOS POR ESCOLA (soma quantidades de todas as unidades)
-- Diferença do modo "por unidade": aqui agrupa tudo da escola em um único orçamento
-- Usa ids_distribuicao (array) em vez de id_distribuicao (único) nos itens
-- Parâmetros: :escola_id, :ids_produtos, :datas_saida, :divisoes_logistica, :dias_uteis_filtro, :ids_formularios, :status_ids, :ids_unidades

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

especificacoes_unidade AS (
    SELECT DISTINCT
        uf.id AS unidade_id,
        uf.cliente_id,
        ef.id AS especificacao_id,
        ef.id_produto,
        ef.corfrente,
        ef.corverso,
        bt.idgruposubstratoimpressao,
        COALESCE(bt.altura, NULLIF(ef.altura, '')::numeric) AS altura_mm,
        COALESCE(bt.largura, NULLIF(ef.largura, '')::numeric) AS largura_mm,
        NULLIF(ef.gramatura_miolo, '') AS gramatura_miolo,
        bg.gramatura AS gramatura_catalogo,
        bg.unidade_medida AS unidade_gramatura,
        dm.quantidade,
        dm.data_saida,
        ap.pares,
        ap.formulario_id,
        ap.nome AS arquivo_nome,
        ap.tipo_arquivo,
        ap.id_componente,
        ap.paginas,
        bi.frente_verso,
        bi."categoria_Prod",
        dm.id_turma,
        t.nome AS nome_turma
    FROM unidades_filtradas uf
    CROSS JOIN parametros p
    JOIN pedido_distribuicoes dm ON dm.unidade_escolar_id = uf.id
    JOIN pedido_especificacoes ef ON ef.id = dm.especificacao_form_id
    LEFT JOIN escola_turmas t ON t.id = dm.id_turma
    LEFT JOIN bremen_gramatura bg ON bg.id = ef.id_gramatura
    LEFT JOIN bremen_tamanho_papel bt ON bt.id = ef.id_papel
    LEFT JOIN pedido_arquivos_pdf ap ON ap.item_pedido_id = ef.id
    LEFT JOIN bremen_itens bi ON bi.id_produto = ef.id_produto
    WHERE dm.quantidade > 0
        AND (p.ids_produtos IS NULL OR ef.id_produto = ANY(p.ids_produtos))
        AND (
            p.datas_saida IS NULL
            OR NULLIF(dm.data_saida, '')::date = ANY(p.datas_saida)
            OR NULLIF(dm.data_saida, '') IS NULL
        )
        AND dm.status_id = ANY(p.status_ids)
        AND (p.ids_formularios IS NULL OR ap.formulario_id = ANY(p.ids_formularios))
        AND (p.ids_arquivos IS NULL OR ap.id = ANY(p.ids_arquivos))
),

distribuicao_ids AS (
    SELECT DISTINCT
        uf.id AS unidade_id,
        dm.id AS distribuicao_material_id,
        ef.id AS especificacao_id,
        COALESCE(ap.pares::text, ef.id::text)
            || '|t:' || COALESCE(dm.id_turma::text, 'null') AS chave_agrupamento,
        ap.pares,
        ap.formulario_id,
        dm.id_turma
    FROM unidades_filtradas uf
    CROSS JOIN parametros p
    JOIN pedido_distribuicoes dm ON dm.unidade_escolar_id = uf.id
    JOIN pedido_especificacoes ef ON ef.id = dm.especificacao_form_id
    LEFT JOIN pedido_arquivos_pdf ap ON ap.item_pedido_id = ef.id
    WHERE dm.quantidade > 0
        AND (p.ids_produtos IS NULL OR ef.id_produto = ANY(p.ids_produtos))
        AND (
            p.datas_saida IS NULL
            OR NULLIF(dm.data_saida, '')::date = ANY(p.datas_saida)
            OR NULLIF(dm.data_saida, '') IS NULL
        )
        AND dm.status_id = ANY(p.status_ids)
        AND (p.ids_formularios IS NULL OR ap.formulario_id = ANY(p.ids_formularios))
        AND (p.ids_arquivos IS NULL OR ap.id = ANY(p.ids_arquivos))
),

-- Primeiro, agrupa as quantidades únicas por cliente/unidade para evitar duplicação causada pelos JOINs
quantidades_unicas AS (
    SELECT DISTINCT
        uf.escola_id,
        uf.cliente_id,
        eu.unidade_id,
        eu.especificacao_id,
        eu.id_produto,
        COALESCE(eu.pares::text, eu.especificacao_id::text)
            || '|t:' || COALESCE(eu.id_turma::text, 'null') AS chave_agrupamento,
        eu.pares,
        eu.formulario_id,
        eu.quantidade,
        eu.tipo_arquivo,
        eu.id_turma,
        eu.nome_turma
    FROM especificacoes_unidade eu
    JOIN unidades_filtradas uf ON uf.id = eu.unidade_id
),

-- Agrupa por cliente para obter uma única quantidade por cliente/item (somente miolo)
quantidades_por_cliente AS (
    SELECT
        qu.escola_id,
        qu.cliente_id,
        qu.especificacao_id,
        qu.id_produto,
        qu.chave_agrupamento,
        qu.pares,
        qu.formulario_id,
        qu.id_turma,
        qu.nome_turma,
        MAX(qu.quantidade) AS quantidade
    FROM quantidades_unicas qu
    -- Filtra para somar apenas miolo, ignorando capa pura
    WHERE LOWER(COALESCE(qu.tipo_arquivo, 'miolo')) LIKE '%miolo%'
    GROUP BY
        qu.escola_id,
        qu.cliente_id,
        qu.especificacao_id,
        qu.id_produto,
        qu.chave_agrupamento,
        qu.pares,
        qu.formulario_id,
        qu.id_turma,
        qu.nome_turma
),

-- Soma as quantidades de todos os clientes para a mesma escola/item (SEM fazer JOIN que multiplica)
quantidades_escola AS (
    SELECT
        qc.escola_id,
        qc.chave_agrupamento,
        qc.pares,
        qc.formulario_id,
        qc.id_turma,
        qc.nome_turma,
        MAX(qc.especificacao_id) AS especificacao_id,
        MAX(qc.id_produto) AS id_produto,
        SUM(qc.quantidade) AS quantidade_total
    FROM quantidades_por_cliente qc
    GROUP BY
        qc.escola_id,
        qc.chave_agrupamento,
        qc.pares,
        qc.formulario_id,
        qc.id_turma,
        qc.nome_turma
),

-- Junta os metadados (nome, altura, etc.) com as quantidades já calculadas
itens_produto AS (
    SELECT
        qe.escola_id,
        qe.chave_agrupamento,
        qe.pares,
        qe.formulario_id,
        qe.especificacao_id,
        qe.id_produto,
        qe.id_turma,
        qe.nome_turma,
        (SELECT uf.client_id_venda FROM unidades_filtradas uf WHERE uf.escola_id = qe.escola_id LIMIT 1) AS client_id_venda,
        (SELECT uf.vendedor_id_venda FROM unidades_filtradas uf WHERE uf.escola_id = qe.escola_id LIMIT 1) AS vendedor_id_venda,
        (SELECT uf.forma_pagamento FROM unidades_filtradas uf WHERE uf.escola_id = qe.escola_id LIMIT 1) AS forma_pagamento_venda,
        COALESCE(
            CASE
                WHEN qe.id_turma IS NOT NULL AND NULLIF(TRIM(qe.nome_turma), '') IS NOT NULL THEN
                    '(#' || TRIM(qe.nome_turma) || ') - '
                    || UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
                        COALESCE(
                            (SELECT eu_nome.arquivo_nome FROM especificacoes_unidade eu_nome
                             WHERE eu_nome.especificacao_id = qe.especificacao_id
                               AND LOWER(eu_nome.tipo_arquivo) = 'miolo'
                             LIMIT 1),
                            (SELECT eu_nome.arquivo_nome FROM especificacoes_unidade eu_nome
                             WHERE eu_nome.especificacao_id = qe.especificacao_id
                             LIMIT 1)
                        ), '\.pdf$', '', 'i'), '[_-]+', ' ', 'g')))
                    || ' - (#' || (SELECT form.id FROM pedido_formularios form WHERE form.id = qe.formulario_id LIMIT 1) || ')'
                ELSE
                    UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
                        COALESCE(
                            (SELECT eu_nome.arquivo_nome FROM especificacoes_unidade eu_nome
                             WHERE eu_nome.especificacao_id = qe.especificacao_id
                               AND LOWER(eu_nome.tipo_arquivo) = 'miolo'
                             LIMIT 1),
                            (SELECT eu_nome.arquivo_nome FROM especificacoes_unidade eu_nome
                             WHERE eu_nome.especificacao_id = qe.especificacao_id
                             LIMIT 1)
                ), '\.pdf$', '', 'i'), '[_-]+', ' ', 'g')))
                    || ' (#' || (SELECT form.id FROM pedido_formularios form WHERE form.id = qe.formulario_id LIMIT 1) || ')'
            END,
            'Produto ' || qe.id_produto
        ) AS nome_arquivo,
        (SELECT MAX(eu_meta.altura_mm) FROM especificacoes_unidade eu_meta WHERE eu_meta.especificacao_id = qe.especificacao_id) AS altura,
        (SELECT MAX(eu_meta.largura_mm) FROM especificacoes_unidade eu_meta WHERE eu_meta.especificacao_id = qe.especificacao_id) AS largura,
        (SELECT MAX(eu_meta.gramatura_miolo) FROM especificacoes_unidade eu_meta WHERE eu_meta.especificacao_id = qe.especificacao_id) AS gramatura_miolo,
        qe.quantidade_total,
        (SELECT MAX(form.observacoes) FROM pedido_formularios form WHERE form.id = qe.formulario_id) AS obs_producao,
        (SELECT MAX(TO_CHAR(form.data_entrega, 'DD/MM/YYYY')) FROM pedido_formularios form WHERE form.id = qe.formulario_id) AS data_entrega_pedido,
        (SELECT MAX(form.titulo) FROM pedido_formularios form WHERE form.id = qe.formulario_id) AS form_titulo,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM especificacoes_unidade eu_tipo
                WHERE eu_tipo.especificacao_id = qe.especificacao_id
                  AND (
                    (eu_tipo.paginas > 2 AND UPPER(eu_tipo.frente_verso) = 'FV' AND UPPER(eu_tipo."categoria_Prod") = 'PROVA')
                    OR (eu_tipo.paginas > 1 AND UPPER(eu_tipo.frente_verso) = 'SF' AND UPPER(eu_tipo."categoria_Prod") = 'PROVA')
                  )
            )
            THEN 'normal'
            ELSE 'separado'
        END AS tipo_agrupamento
    FROM quantidades_escola qe
),

itens AS (
    SELECT DISTINCT
        eu.pares,
        eu.formulario_id,
        eu.especificacao_id,
        eu.id_produto,
        eu.corfrente,
        eu.corverso,
        eu.idgruposubstratoimpressao,
        bi.descricao,
        bi.sub_grupo,
        bi."categoria_Prod",
        eu.altura_mm,
        eu.largura_mm,
        eu.gramatura_miolo,
        eu.gramatura_catalogo,
        eu.unidade_gramatura
    FROM especificacoes_unidade eu
    JOIN bremen_itens bi ON bi.id_produto = eu.id_produto
),

componentes AS (
    SELECT DISTINCT
        i.pares,
        i.formulario_id,
        i.especificacao_id,
        i.id_produto,
        i.corfrente,
        i.corverso,
        i.idgruposubstratoimpressao,
        i.sub_grupo,
        i."categoria_Prod",
        bc.id AS componente_id,
        bc.id_componente,
        bc.descricao,
        bc.is_capa,
        bc.is_miolo,
        ROUND(i.altura_mm::numeric / 10, 2) AS altura,
        ROUND(i.largura_mm::numeric / 10, 2) AS largura,
        i.gramatura_miolo,
        i.gramatura_catalogo,
        CASE
            WHEN (bc.is_capa IS TRUE OR LOWER(COALESCE(bc.descricao, '')) LIKE '%%capa%%')
             AND (bc.is_miolo IS TRUE OR LOWER(COALESCE(bc.descricao, '')) LIKE '%%miolo%%') THEN (
                SELECT COALESCE(ap_pag.paginas, 0)
                FROM pedido_arquivos_pdf ap_pag
                WHERE ap_pag.id_componente = bc.id_componente
                  AND (
                      (i.pares IS NOT NULL AND ap_pag.pares = i.pares AND ap_pag.formulario_id = i.formulario_id)
                      OR (i.pares IS NULL AND ap_pag.item_pedido_id = i.especificacao_id)
                  )
                ORDER BY
                    CASE WHEN LOWER(COALESCE(ap_pag.tipo_arquivo, '')) = 'miolo' THEN 0 ELSE 1 END,
                    ap_pag.criado_em DESC
                LIMIT 1
            )
            WHEN (bc.is_capa IS TRUE OR LOWER(COALESCE(bc.descricao, '')) LIKE '%%capa%%')
             AND NOT (bc.is_miolo IS TRUE OR LOWER(COALESCE(bc.descricao, '')) LIKE '%%miolo%%') THEN 1
            WHEN (bc.is_miolo IS TRUE OR LOWER(COALESCE(bc.descricao, '')) LIKE '%%miolo%%')
             AND NOT (bc.is_capa IS TRUE OR LOWER(COALESCE(bc.descricao, '')) LIKE '%%capa%%') THEN (
                SELECT COALESCE(ap_pag.paginas, 0)
                FROM pedido_arquivos_pdf ap_pag
                WHERE ap_pag.id_componente = bc.id_componente
                  AND (
                      (i.pares IS NOT NULL AND ap_pag.pares = i.pares AND ap_pag.formulario_id = i.formulario_id)
                      OR (i.pares IS NULL AND ap_pag.item_pedido_id = i.especificacao_id)
                  )
                ORDER BY
                    CASE WHEN LOWER(COALESCE(ap_pag.tipo_arquivo, '')) = 'miolo' THEN 0 ELSE 1 END,
                    ap_pag.criado_em DESC
                LIMIT 1
            )
            ELSE (
                SELECT ap_pag.paginas
                FROM pedido_arquivos_pdf ap_pag
                WHERE ap_pag.id_componente = bc.id_componente
                  AND (
                      (i.pares IS NOT NULL AND ap_pag.pares = i.pares AND ap_pag.formulario_id = i.formulario_id)
                      OR (i.pares IS NULL AND ap_pag.item_pedido_id = i.especificacao_id)
                  )
                ORDER BY ap_pag.criado_em DESC
                LIMIT 1
            )
        END AS quantidade_paginas
    FROM itens i
    JOIN bremen_componentes bc ON bc.id_produto = i.id_produto
    LEFT JOIN pedido_arquivos_pdf ap_sel
        ON ap_sel.item_pedido_id = i.especificacao_id
       AND ap_sel.id_componente = bc.id_componente
       AND (
            (i.pares IS NOT NULL AND ap_sel.pares = i.pares AND ap_sel.formulario_id = i.formulario_id)
            OR (i.pares IS NULL AND ap_sel.pares IS NULL AND (ap_sel.formulario_id = i.formulario_id OR ap_sel.formulario_id IS NULL))
       )
),

respostas_componentes AS (
    SELECT DISTINCT ON (c.especificacao_id, c.id_componente, bp.id)
        c.pares,
        c.formulario_id,
        c.especificacao_id,
        c.id_produto,
        c.id_componente,
        bp.id AS pergunta_id,
        br.descricao_opcao  AS resposta
    FROM componentes c
    JOIN bremen_perguntas bp ON bp.id_componente = c.id_componente
    LEFT JOIN bremen_especificacao_detalhes bed
        ON bed.pergunta_id = bp.id
        AND bed.especificacao_id = c.especificacao_id
    LEFT JOIN bremen_respostas br ON br.id = bed.resposta_id
    WHERE br.valor IS NOT NULL
    ORDER BY c.especificacao_id, c.id_componente, bp.id
),

respostas_gerais AS (
    SELECT DISTINCT ON (i.especificacao_id, bp.id)
        i.pares,
        i.formulario_id,
        i.especificacao_id,
        i.id_produto,
        bp.id AS pergunta_id,
        br.descricao_opcao  AS resposta
    FROM itens i
    JOIN bremen_perguntas bp ON bp.id_geral = i.id_produto
    LEFT JOIN bremen_especificacao_detalhes bed
        ON bed.pergunta_id = bp.id
        AND bed.especificacao_id = i.especificacao_id
    LEFT JOIN bremen_respostas br ON br.id = bed.resposta_id
    WHERE br.valor IS NOT NULL
    ORDER BY i.especificacao_id, bp.id
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

SELECT json_build_object(
    'identifier', 'PageFlow',
    'data', json_build_object(
        'id_escola', ip.escola_id,
        'id_cliente', ip.client_id_venda,
        'id_vendedor', ip.vendedor_id_venda,
        'id_forma_pagamento', ip.forma_pagamento_venda,
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
                    'ids_distribuicao', COALESCE((
                        SELECT json_agg(DISTINCT di_sub.distribuicao_material_id)
                        FROM distribuicao_ids di_sub
                        WHERE (
                            (ip.pares IS NOT NULL AND di_sub.pares = ip.pares AND di_sub.formulario_id = ip.formulario_id)
                            OR (ip.pares IS NULL AND di_sub.chave_agrupamento = ip.chave_agrupamento)
                        )
                        AND di_sub.id_turma IS NOT DISTINCT FROM ip.id_turma
                    ), '[]'::json),
                    'componentes', COALESCE((
                        SELECT json_agg(
                            CASE
                                -- ==========================================================
                                -- CENÁRIO 1: MIOLO
                                -- ==========================================================
                                WHEN (comp_sel.is_miolo IS TRUE OR LOWER(COALESCE(comp_sel.descricao, '')) LIKE '%miolo%') THEN
                                    json_strip_nulls(json_build_object(
                                        'id', comp_sel.id_componente,
                                        'descricao', comp_sel.descricao,
                                        'altura', comp_sel.altura,
                                        'largura', comp_sel.largura,
                                        'quantidade_paginas', COALESCE(comp_sel.quantidade_paginas, 0),
                                        'idgruposubstratoimpressao', comp_sel.idgruposubstratoimpressao,
                                        'gramaturasubstratoimpressao', COALESCE(
                                            comp_sel.gramatura_catalogo,
                                            NULLIF(replace(regexp_replace(comp_sel.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric
                                        ),
                                        'corfrente', comp_sel.corfrente,
                                        'corverso', comp_sel.corverso,
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
                                                AND rc.id_componente = comp_sel.id_componente
                                                AND rc.especificacao_id = comp_sel.especificacao_id
                                            WHERE bp.id_componente = comp_sel.id_componente
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
                                            WHERE tc.especificacao_id = comp_sel.especificacao_id
                                        ), '[]'::json)
                                    ))

                                -- ==========================================================
                                -- CENÁRIO 2: CAPA (Ajuste para ILIKE e prioridade de dados)
                                -- ==========================================================
                                WHEN (comp_sel.is_capa IS TRUE OR LOWER(COALESCE(comp_sel.descricao, '')) LIKE '%capa%') THEN
                                    json_strip_nulls(
                                        json_build_object(
                                            'id', comp_sel.id_componente,
                                            'descricao', comp_sel.descricao,
                                            'altura', comp_sel.altura,
                                            'largura', comp_sel.largura,
                                            'quantidade_paginas', comp_sel.quantidade_paginas,
                                                'idgruposubstratoimpressao',
                                                    CASE
                                                        WHEN UPPER(comp_sel."categoria_Prod") = 'LIVRETO'
                                                             AND comp_sel.is_capa IS TRUE
                                                             AND EXISTS (
                                                                 SELECT 1 FROM componentes c_miolo
                                                                 WHERE c_miolo.id_produto = comp_sel.id_produto
                                                                   AND c_miolo.is_miolo IS TRUE
                                                                   AND (
                                                                       (ip.pares IS NOT NULL AND c_miolo.pares = ip.pares AND c_miolo.formulario_id = ip.formulario_id)
                                                                       OR (ip.pares IS NULL AND c_miolo.especificacao_id = ip.especificacao_id)
                                                                   )
                                                             )
                                                        THEN comp_sel.idgruposubstratoimpressao
                                                        ELSE NULL
                                                    END,
                                            'gramaturasubstratoimpressao',
                                                CASE
                                                    -- Verifica se é Livreto (categoria_Prod) E se tem capa e miolo
                                                    WHEN UPPER(comp_sel."categoria_Prod") = 'LIVRETO'
                                                         AND comp_sel.is_capa IS TRUE
                                                         AND EXISTS (
                                                             SELECT 1 FROM componentes c_miolo
                                                             WHERE c_miolo.id_produto = comp_sel.id_produto
                                                               AND c_miolo.is_miolo IS TRUE
                                                               AND (
                                                                   (ip.pares IS NOT NULL AND c_miolo.pares = ip.pares AND c_miolo.formulario_id = ip.formulario_id)
                                                                   OR (ip.pares IS NULL AND c_miolo.especificacao_id = ip.especificacao_id)
                                                               )
                                                         )
                                                    THEN
                                                        COALESCE(
                                                            comp_sel.gramatura_catalogo,
                                                            NULLIF(replace(regexp_replace(comp_sel.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric
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
                                                    AND rc.id_componente = comp_sel.id_componente
                                                    AND rc.especificacao_id = comp_sel.especificacao_id
                                                WHERE bp.id_componente = comp_sel.id_componente
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
                                                WHERE tc.especificacao_id = comp_sel.especificacao_id
                                            ), '[]'::json)
                                        )
                                    )

                                -- ==========================================================
                                -- CENÁRIO 3: OUTROS
                                -- ==========================================================
                                ELSE
                                    json_build_object(
                                        'id', comp_sel.id_componente,
                                        'descricao', comp_sel.descricao,
                                        'altura', comp_sel.altura,
                                        'largura', comp_sel.largura,
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
                                                AND rc.id_componente = comp_sel.id_componente
                                                AND rc.especificacao_id = comp_sel.especificacao_id
                                            WHERE bp.id_componente = comp_sel.id_componente
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
                                            WHERE tc.especificacao_id = comp_sel.especificacao_id
                                        ), '[]'::json)
                                    )
                            END
                        )
                        FROM (
                            SELECT DISTINCT ON (comp.id_componente)
                                comp.componente_id,
                                comp.id_componente,
                                comp.id_produto,
                                comp.descricao,
                                comp.altura,
                                comp.largura,
                                comp.gramatura_miolo,
                                comp.gramatura_catalogo,
                                comp.quantidade_paginas,
                                comp.especificacao_id,
                                comp.corfrente,
                                comp.corverso,
                                comp.idgruposubstratoimpressao,
                                comp.sub_grupo,
                                comp."categoria_Prod",
                                comp.is_capa,
                                comp.is_miolo
                            FROM componentes comp
                            WHERE (
                                (ip.pares IS NOT NULL AND comp.pares = ip.pares AND comp.formulario_id = ip.formulario_id)
                                OR (ip.pares IS NULL AND comp.especificacao_id = ip.especificacao_id)
                            )
                            ORDER BY comp.id_componente,
                                -- !!! ALTERAÇÃO CRUCIAL AQUI EMBAIXO !!!
                                -- Prioriza linhas que tenham gramatura preenchida
                                CASE WHEN comp.gramatura_catalogo IS NOT NULL OR comp.gramatura_miolo IS NOT NULL THEN 0 ELSE 1 END,
                                CASE WHEN EXISTS (SELECT 1 FROM respostas_componentes rc_pref WHERE rc_pref.id_componente = comp.id_componente AND rc_pref.especificacao_id = comp.especificacao_id) THEN 0 ELSE 1 END,
                                comp.especificacao_id
                        ) comp_sel
                    ), '[]'::json),
                    'perguntas_gerais', COALESCE((
                        SELECT json_agg(
                            json_build_object(
                              'tipo', bp.tipo,
                              'pergunta', bp.nome,
                              'resposta', rg.resposta,
                              'id_pergunta', bp.id_pergunta
                            )
                        )
                        FROM bremen_perguntas bp
                        INNER JOIN respostas_gerais rg ON rg.pergunta_id = bp.id AND rg.especificacao_id = ip.especificacao_id
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
                        WHERE tg.especificacao_id = ip.especificacao_id
                    ), '[]'::json)
                )
                ORDER BY ip.chave_agrupamento
            ), '[]'::json
        )
    )
)
FROM itens_produto ip
GROUP BY ip.escola_id, ip.id_turma, ip.nome_turma, ip.tipo_agrupamento, ip.client_id_venda, ip.vendedor_id_venda, ip.forma_pagamento_venda
ORDER BY ip.escola_id, ip.id_turma NULLS FIRST, ip.tipo_agrupamento DESC;
