-- Query para geração de orçamentos por unidade escolar
-- Parâmetros: :escola_id, :ids_produtos, :datas_saida, :divisoes_logistica, :dias_uteis_filtro, :ids_formularios, :status_ids, :ids_unidades, :ids_arquivos
--
-- Reescrita para a nova arquitetura de distribuição (migrations 20260831120000 e
-- 20260831130000 do PAGEFLOW):
--   - pedido_distribuicoes agora é 1 linha por item comercial entregue a um
--     destino (unidade/turma) — não existe mais 1 linha por arquivo/componente,
--     então quantidade nunca vem duplicada e não precisa mais ser filtrada por
--     "miolo" pra evitar contagem dupla.
--   - pedido_distribuicao_arquivos vincula 1+ arquivos (pedido_arquivos_pdf) a
--     cada distribuição, já carregando o id_componente correto — não precisa
--     mais adivinhar qual PDF é capa/miolo por `pares`/`item_pedido_id` com
--     DISTINCT ON e prioridade de ORDER BY.
--   - bremen_especificacao_detalhes foi renomeada para pedido_pergunta_resposta.

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
        ue.vendedor_id
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
        uf.cliente_id,
        uf.client_id_venda,
        uf.vendedor_id,
        uf.forma_pagamento,
        uf.nome AS nome_unidade,
        dm.formulario_id,
        dm.pedido_item_carrinho_id,
        dm.quantidade,
        dm.id_turma,
        t.nome AS nome_turma,
        t.area AS area_turma
    FROM unidades_filtradas uf
    CROSS JOIN parametros p
    JOIN pedido_distribuicoes dm ON dm.unidade_escolar_id = uf.id
    LEFT JOIN escola_turmas t ON t.id = dm.id_turma
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

-- Agrega os metadados do item comercial a partir dos seus materiais.
-- Uma distribuição = um item no orçamento; nunca precisa de GROUP BY por
-- chave heurística porque dm.id já é a chave certa.
itens_produto AS (
    SELECT
        d.distribuicao_id,
        d.unidade_id,
        d.cliente_id,
        d.client_id_venda,
        d.vendedor_id,
        d.forma_pagamento,
        d.nome_unidade,
        d.formulario_id,
        d.pedido_item_carrinho_id,
        d.quantidade AS quantidade_total,
        d.id_turma,
        d.nome_turma,
        d.area_turma,
        MAX(mat.id_produto) AS id_produto,
        -- Prefere a especificação do miolo pra perguntas/tarefas de escopo geral do item.
        COALESCE(
            MAX(CASE WHEN mat.is_miolo THEN mat.especificacao_form_id END),
            MAX(mat.especificacao_form_id)
        ) AS especificacao_id_geral,
        (
            CASE
                WHEN d.id_turma IS NOT NULL AND NULLIF(TRIM(d.nome_turma), '') IS NOT NULL THEN
                    '(*' || TRIM(d.nome_turma) || ') - '
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
            END
        ) AS nome_arquivo,
        MAX(mat.altura_mm) AS altura,
        MAX(mat.largura_mm) AS largura,
        MAX(mat.gramatura_miolo) AS gramatura_miolo,
        MAX(form.observacoes) AS obs_producao,
        MAX(TO_CHAR(form.data_entrega, 'DD/MM/YYYY')) AS data_entrega_pedido,
        -- Campos extras para obs_producao condicional (cliente_id = 151)
        MAX(form.titulo) AS form_titulo,
        MAX(form.criado_em::text) AS data_pedido,
        MAX(u.nome) AS solicitante_nome,
        MAX(u.email) AS solicitante_email,
        COALESCE(
            MAX(CASE WHEN mat.is_miolo THEN mat.arquivo_nome END),
            MAX(mat.arquivo_nome)
        ) AS arquivo_nome_raw,
        CASE
            WHEN (MAX(mat.paginas) > 2 AND UPPER(MAX(mat.frente_verso)) = 'FV' AND UPPER(MAX(mat."categoria_Prod")) = 'PROVA')
              OR (MAX(mat.paginas) > 1 AND UPPER(MAX(mat.frente_verso)) = 'SF' AND UPPER(MAX(mat."categoria_Prod")) = 'PROVA')
            THEN 'normal'
            ELSE 'separado'
        END AS tipo_agrupamento
    FROM distribuicoes d
    JOIN materiais mat ON mat.distribuicao_id = d.distribuicao_id
    JOIN pedido_formularios form ON form.id = d.formulario_id
    LEFT JOIN usuarios u ON u.id = form.usuario_id
    GROUP BY
        d.distribuicao_id, d.unidade_id, d.cliente_id, d.client_id_venda, d.vendedor_id,
        d.forma_pagamento, d.nome_unidade, d.formulario_id, d.pedido_item_carrinho_id,
        d.quantidade, d.id_turma, d.nome_turma, d.area_turma
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
    SELECT DISTINCT ON (ip.distribuicao_id, bp.id)
        ip.distribuicao_id,
        bp.id AS pergunta_id,
        br.descricao_opcao AS resposta
    FROM itens_produto ip
    JOIN bremen_perguntas bp ON bp.id_geral = ip.id_produto
    LEFT JOIN pedido_pergunta_resposta pr
        ON pr.pergunta_id = bp.id
        AND pr.especificacao_id = ip.especificacao_id_geral
    LEFT JOIN bremen_respostas br ON br.id = pr.resposta_id
    WHERE br.valor IS NOT NULL
    ORDER BY ip.distribuicao_id, bp.id
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
        'id_cliente', ip.cliente_id,
        'id_vendedor', ip.vendedor_id,
        'id_forma_pagamento', ip.forma_pagamento,
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
                                -- ESSA CONDIÇÃO MOSTRAR PARA TODOS OS COLEGIOS NA OP, NAO SOMENTE O SANTA CATARINA
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
                        ELSE CONCAT_WS(
                            CHR(10) || CHR(10),
                            ip.obs_producao,
                            CONCAT_WS(
                                CHR(10),
                                'Data de Entrega: ' || COALESCE(ip.data_entrega_pedido, '-'),
                                'Título: ' || COALESCE(ip.form_titulo, '-')
                            )
                        )
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
                                WHEN mat.is_miolo THEN
                                    json_strip_nulls(json_build_object(
                                        'id', mat.id_componente,
                                        'id_distribuicao', ip.distribuicao_id,
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
                                            'id_distribuicao', ip.distribuicao_id,
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
                                        'id_distribuicao', ip.distribuicao_id,
                                        'descricao', mat.componente_descricao,
                                        'altura', ROUND(mat.altura_mm::numeric / 10, 2),
                                        'largura', ROUND(mat.largura_mm::numeric / 10, 2),
                                        'gramaturasubstratoimpressao',
                                            CASE WHEN LOWER(mat.componente_descricao) LIKE '%folha%rosto%'
                                            THEN COALESCE(mat.gramatura_catalogo, NULLIF(replace(regexp_replace(mat.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric)
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
                        WHERE mat.distribuicao_id = ip.distribuicao_id
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
                           AND rg.distribuicao_id = ip.distribuicao_id
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
                ORDER BY ip.distribuicao_id
            ), '[]'::json
        )
    )
)
FROM itens_produto ip
GROUP BY ip.unidade_id, ip.cliente_id, ip.tipo_agrupamento, ip.client_id_venda, ip.vendedor_id, ip.forma_pagamento, ip.nome_unidade
ORDER BY ip.unidade_id, ip.tipo_agrupamento DESC;
