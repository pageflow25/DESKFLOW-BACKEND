-- Orçamento no modo POR ESCOLA (orcamento_api_lotes.modo_agrupamento = 'escola').
--
-- Base: docs/legado/query_orcamento_escola.sql. Monta o corpo de UM POST
-- /api/v1/orcamento para UM orcamento_api_orcamentos do PCP — que o PageFlow
-- já criou com os pedidos de uma única turma (ou dos pedidos sem turma) de um
-- mesmo cliente/vendedor/forma de pagamento. Soma as quantidades de todas as
-- unidades que compartilham o mesmo item de carrinho.
--
-- Diferenças para a query legada:
--   - Entrada: os ids exatos de pedido_distribuicoes do orçamento, não mais
--     filtros por escola/data/status.
--   - Cabeçalho (id_cliente/id_vendedor/id_forma_pagamento) vem da requisição
--     (par escolhido no "Enviar"), não de uma unidade qualquer com LIMIT 1.
--   - Cada item leva `codigo_externo` = ids dos pedidos agrupados, separados
--     por vírgula ("12345683,12345687"); o PageFlow desmembra no retorno.
--   - `materiais` lê o papel em três eixos, com id_papel de reserva.
--   - `tarefas_gerais` sai como { id, descricao } (formato do Wingraph).
--   - Campos internos (ids_distribuicao, id_escola) não vão mais no corpo.
--
-- Parâmetros: :pedido_distribuicao_ids (int[]), :id_cliente, :id_vendedor,
-- :id_forma_pagamento. Devolve 1 linha por turma — para um orçamento do PCP,
-- exatamente 1 (o serviço confere).

WITH parametros AS (
    SELECT
        CAST(:pedido_distribuicao_ids AS int[]) AS pedido_distribuicao_ids,
        CAST(:id_cliente AS int) AS id_cliente,
        CAST(:id_vendedor AS int) AS id_vendedor,
        CAST(:id_forma_pagamento AS text) AS id_forma_pagamento
),

-- 1 linha por item comercial entregue a uma unidade/turma.
distribuicoes AS (
    SELECT
        dm.id AS distribuicao_id,
        dm.unidade_escolar_id AS unidade_id,
        dm.formulario_id,
        dm.pedido_item_carrinho_id,
        dm.quantidade,
        dm.id_turma
    FROM pedido_distribuicoes dm
    CROSS JOIN parametros p
    WHERE dm.id = ANY(p.pedido_distribuicao_ids)
      AND dm.quantidade > 0
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
        COALESCE(ef.id_substrato, bt.idgruposubstratoimpressao) AS idgruposubstratoimpressao,
        COALESCE(bf.altura, bt.altura, NULLIF(ef.altura, '')::numeric) AS altura_mm,
        COALESCE(bf.largura, bt.largura, NULLIF(ef.largura, '')::numeric) AS largura_mm,
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
    LEFT JOIN bremen_formato_papel bf ON bf.id = ef.id_formato
    LEFT JOIN bremen_tamanho_papel bt ON bt.id = ef.id_papel
    WHERE pda.distribuicao_material_id IN (SELECT distribuicao_id FROM distribuicoes)
),

-- Soma as quantidades de todas as unidades que compartilham o mesmo item de
-- carrinho (mesmo item comercial, distribuído em vários destinos).
itens_agrupados AS (
    SELECT
        d.pedido_item_carrinho_id,
        d.id_turma,
        MAX(d.formulario_id) AS formulario_id,
        MIN(d.distribuicao_id) AS distribuicao_referencia,
        SUM(d.quantidade) AS quantidade_total,
        array_to_string(array_agg(DISTINCT d.distribuicao_id ORDER BY d.distribuicao_id), ',') AS codigo_externo
    FROM distribuicoes d
    GROUP BY d.pedido_item_carrinho_id, d.id_turma
),

-- Metadados do item comercial a partir dos materiais da distribuição de
-- referência (todas as distribuições do grupo compartilham o mesmo item de
-- carrinho, logo os mesmos arquivos/especificações).
itens_produto AS (
    SELECT
        ia.pedido_item_carrinho_id,
        ia.id_turma,
        ia.formulario_id,
        ia.distribuicao_referencia,
        ia.quantidade_total,
        ia.codigo_externo,
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
        MAX(form.observacoes) AS obs_producao,
        MAX(TO_CHAR(form.data_entrega, 'DD/MM/YYYY')) AS data_entrega_pedido,
        MAX(form.titulo) AS form_titulo
    FROM itens_agrupados ia
    LEFT JOIN escola_turmas t ON t.id = ia.id_turma
    JOIN materiais mat ON mat.distribuicao_id = ia.distribuicao_referencia
    JOIN pedido_formularios form ON form.id = ia.formulario_id
    GROUP BY
        ia.pedido_item_carrinho_id, ia.id_turma, ia.formulario_id, ia.distribuicao_referencia,
        ia.quantidade_total, ia.codigo_externo, t.nome, t.area
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
        bt.descricao
    FROM pedido_especificacoes_tarefas bet
    JOIN bremen_tarefas bt ON bt.id = bet.tarefa_id
    WHERE bt.id_componente IS TRUE
),

-- Tarefas de escopo geral vinculadas à especificação via tabela pivot
tarefas_gerais AS (
    SELECT DISTINCT
        bet.especificacao_id,
        bt.id_tarefa,
        bt.descricao
    FROM pedido_especificacoes_tarefas bet
    JOIN bremen_tarefas bt ON bt.id = bet.tarefa_id
    WHERE bt.id_geral IS TRUE
)

SELECT json_strip_nulls(json_build_object(
    'data', json_build_object(
        'id_cliente', p.id_cliente,
        'id_vendedor', p.id_vendedor,
        'id_forma_pagamento', p.id_forma_pagamento,
        'itens', COALESCE(
            json_agg(
                json_build_object(
                    'id_produto', ip.id_produto,
                    'titulo', ip.nome_arquivo,
                    'codigo_externo', ip.codigo_externo,
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
                            -- Ordem estável: componentes que não são miolo antes do miolo.
                            ORDER BY mat.is_miolo, mat.id_componente, mat.arquivo_pdf_id
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
                                ORDER BY bp.id_pergunta
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
                                'id', tg.id_tarefa,
                                'descricao', tg.descricao
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
)) AS payload
FROM itens_produto ip
CROSS JOIN parametros p
GROUP BY ip.id_turma, p.id_cliente, p.id_vendedor, p.id_forma_pagamento
ORDER BY ip.id_turma NULLS FIRST;
