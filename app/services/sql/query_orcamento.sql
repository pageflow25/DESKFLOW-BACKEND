-- Query para geração de orçamentos por unidade escolar
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
        ue.vendedor_id  -- switched from vendedor_id_venda to vendedor_id
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
        
        dm.id AS id_distribuicao, 
        dm.id_turma,
        t.nome AS nome_turma,
        t.area AS area_turma,
        
        ap.pares,
        ap.formulario_id,
        ap.nome AS arquivo_nome,
        ap.tipo_arquivo,
        ap.id_componente,
        ap.paginas,
        bi.frente_verso,
        bi."categoria_Prod"
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

itens_produto AS (
    SELECT
        eu.unidade_id,
        eu.cliente_id,
        eu.id_turma,
        eu.nome_turma,
        -- Inclui id_turma na chave de agrupamento para gerar um item por turma
        COALESCE(eu.pares::text, eu.especificacao_id::text)
            || '|t:' || COALESCE(eu.id_turma::text, 'null') AS chave_agrupamento,
        eu.pares,
        eu.formulario_id,
        MAX(eu.especificacao_id) AS especificacao_id,
        MAX(eu.id_produto) AS id_produto,
        (SELECT uf.client_id_venda FROM unidades_filtradas uf WHERE uf.id = eu.unidade_id LIMIT 1) AS client_id_venda,
        (SELECT uf.vendedor_id FROM unidades_filtradas uf WHERE uf.id = eu.unidade_id LIMIT 1) AS vendedor_id,
        (SELECT uf.forma_pagamento FROM unidades_filtradas uf WHERE uf.id = eu.unidade_id LIMIT 1) AS forma_pagamento_venda,
        (SELECT uf.nome FROM unidades_filtradas uf WHERE uf.id = eu.unidade_id LIMIT 1) AS nome_unidade,
        
        -- PEGA O ID DA DISTRIBUIÇÃO POR TIPO DE COMPONENTE
        MAX(CASE WHEN LOWER(eu.tipo_arquivo) = 'miolo' THEN eu.id_distribuicao END) AS id_distribuicao_miolo,
        MAX(CASE WHEN LOWER(eu.tipo_arquivo) = 'capa'  THEN eu.id_distribuicao END) AS id_distribuicao_capa,
        MAX(eu.id_distribuicao) AS id_distribuicao,
        
        (
            CASE
                WHEN eu.id_turma IS NOT NULL AND NULLIF(TRIM(eu.nome_turma), '') IS NOT NULL THEN
                    '(*' || TRIM(eu.nome_turma) || ') - '
                    || UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
                        COALESCE(
                            MAX(CASE WHEN LOWER(eu.tipo_arquivo) = 'miolo' THEN eu.arquivo_nome END),
                            MAX(eu.arquivo_nome)
                        ), '\.pdf$', '', 'i'), '[_-]+', ' ', 'g')))
                    || ' - (#' || MAX(form.id) || ')'
                ELSE
                    UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(
                        COALESCE(
                            MAX(CASE WHEN LOWER(eu.tipo_arquivo) = 'miolo' THEN eu.arquivo_nome END),
                            MAX(eu.arquivo_nome)
                        ), '\.pdf$', '', 'i'), '[_-]+', ' ', 'g')))
                    || ' (#' || MAX(form.id) || ')'
            END
        ) AS nome_arquivo,
        MAX(eu.altura_mm) AS altura,
        MAX(eu.largura_mm) AS largura,
        MAX(eu.gramatura_miolo) AS gramatura_miolo,
        MAX(eu.quantidade) AS quantidade_total,
        MAX(form.observacoes) AS obs_producao,
        -- Campos extras para obs_producao condicional (cliente_id = 151)
        MAX(form.titulo) AS form_titulo,
        MAX(form.criado_em::text) AS data_pedido,
        MAX(u.nome) AS solicitante_nome,
        MAX(u.email) AS solicitante_email,
        MAX(eu.area_turma) AS area_turma,
        COALESCE(
            MAX(CASE WHEN LOWER(eu.tipo_arquivo) = 'miolo' THEN eu.arquivo_nome END),
            MAX(eu.arquivo_nome)
        ) AS arquivo_nome_raw,
        CASE
            WHEN (MAX(eu.paginas) > 2 AND UPPER(MAX(eu.frente_verso)) = 'FV' AND UPPER(MAX(eu."categoria_Prod")) = 'PROVA')
              OR (MAX(eu.paginas) > 1 AND UPPER(MAX(eu.frente_verso)) = 'SF' AND UPPER(MAX(eu."categoria_Prod")) = 'PROVA')
            THEN 'normal'
            ELSE 'separado'
        END AS tipo_agrupamento
    FROM especificacoes_unidade eu
    JOIN pedido_formularios form ON form.id = eu.formulario_id
    LEFT JOIN usuarios u ON u.id = form.usuario_id
    GROUP BY
        eu.unidade_id,
        eu.cliente_id,
        eu.id_turma,
        eu.nome_turma,
        COALESCE(eu.pares::text, eu.especificacao_id::text)
            || '|t:' || COALESCE(eu.id_turma::text, 'null'),
        eu.pares,
        eu.formulario_id
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
        'id_cliente', ip.cliente_id,
        'id_vendedor', ip.vendedor_id,
        'id_forma_pagamento', ip.forma_pagamento_venda,
        'nome_unidade', ip.nome_unidade,
        'itens', COALESCE(
            json_agg(
                json_build_object(
                    'id_produto', ip.id_produto,
                    'titulo', ip.nome_arquivo,
                    'obs_producao', CASE
                        WHEN ip.cliente_id = 151 THEN
                            CONCAT_WS(
                                CHR(10) || CHR(10),
                                ip.obs_producao,
                                CONCAT_WS(
                                    CHR(10),
                                    'Turma: ' || COALESCE(ip.nome_turma, '-'),
                                    'Segmento: ' || COALESCE(ip.area_turma, '-'),
                                    'Solicitante: ' || COALESCE(ip.solicitante_nome, '-'),
                                    'E-mail: ' || COALESCE(ip.solicitante_email, '-'),
                                    'Arquivo: ' || COALESCE(ip.arquivo_nome_raw, '-'),
                                    'Data do Pedido: ' || COALESCE(ip.data_pedido, '-'),
                                    'Título: ' || COALESCE(ip.form_titulo, '-')
                                )
                            )
                        ELSE ip.obs_producao
                    END,
                    'quantidade', ip.quantidade_total,
                    'usar_listapreco', 1,
                    'manter_estrutura_mod_produto', 1,
                    'componentes', COALESCE((
                        SELECT json_agg(
                            CASE
                                -- ==========================================================
                                -- CENÁRIO 1: MIOLO
                                -- ==========================================================
                                WHEN (comp_sel.is_miolo IS TRUE OR LOWER(COALESCE(comp_sel.descricao, '')) LIKE '%%miolo%%') THEN
                                    json_build_object(
                                        'id', comp_sel.id_componente,
                                        'id_distribuicao', COALESCE(ip.id_distribuicao_miolo, ip.id_distribuicao),
                                        'descricao', comp_sel.descricao,
                                        'altura', comp_sel.altura,
                                        'largura', comp_sel.largura,
                                        'quantidade_paginas', COALESCE(comp_sel.quantidade_paginas, 0),
                                        'idgruposubstratoimpressao',  comp_sel.idgruposubstratoimpressao,
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
                                    )

                                -- ==========================================================
                                -- CENÁRIO 2: CAPA
                                -- ==========================================================
                                WHEN (comp_sel.is_capa IS TRUE OR LOWER(COALESCE(comp_sel.descricao, '')) LIKE '%%capa%%') THEN
                                    json_strip_nulls(
                                        json_build_object(
                                            'id', comp_sel.id_componente,
                                            'id_distribuicao', COALESCE(ip.id_distribuicao_capa, ip.id_distribuicao),
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
                                        'id_distribuicao', ip.id_distribuicao,
                                        'descricao', comp_sel.descricao,
                                        'altura', comp_sel.altura,
                                        'largura', comp_sel.largura,
                                        'gramaturasubstratoimpressao', 
                                            CASE WHEN LOWER(comp_sel.descricao) LIKE '%folha%rosto%' 
                                            THEN COALESCE(comp_sel.gramatura_catalogo, NULLIF(replace(regexp_replace(comp_sel.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric)
                                            ELSE NULL END,
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
GROUP BY ip.unidade_id, ip.cliente_id, ip.tipo_agrupamento, ip.client_id_venda, ip.vendedor_id, ip.forma_pagamento_venda, ip.nome_unidade
ORDER BY ip.unidade_id, ip.tipo_agrupamento DESC;