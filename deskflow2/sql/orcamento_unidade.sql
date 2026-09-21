-- Orçamento no modo NORMAL (orcamento_api_lotes.modo_agrupamento = 'unidade').
--
-- Base: docs/legado/query_orcamento.sql. Monta o corpo de UM POST /api/v1/orcamento
-- para UM orcamento_api_orcamentos do PCP — que o PageFlow já criou com os
-- pedidos de uma única unidade escolar. Diferenças para a query legada:
--   - Entrada: os ids exatos de pedido_distribuicoes do orçamento
--     (orcamento_api_itens), não mais filtros por escola/data/status. Nada é
--     re-selecionado: some o vazamento de "data_saida IS NULL" e o filtro de
--     status (os pedidos já estão em "enviado para o bremen").
--   - Cabeçalho (id_cliente/id_vendedor/id_forma_pagamento) vem da requisição,
--     conforme o par escolhido no clique "Enviar" (venda ou cadastro).
--   - Cada item leva `codigo_externo` = id do pedido_distribuicao: é por ele que
--     o PageFlow liga os valores do retorno a cada pedido.
--   - `materiais` lê o papel em três eixos (id_substrato / bremen_formato_papel),
--     com id_papel (bremen_tamanho_papel) de reserva para pedidos antigos
--     (BACKEND_PAGEFLOW/docs/papel-tres-eixos-deskflow.md).
--   - `tarefas_gerais` sai como { id, descricao } (formato do Wingraph).
--   - Campos internos (id_distribuicao, nome_unidade) não vão mais no corpo.
--
-- Parâmetros: :pedido_distribuicao_ids (int[]), :id_cliente, :id_vendedor,
-- :id_forma_pagamento. Devolve 1 linha por unidade — para um orçamento do PCP,
-- exatamente 1 (o serviço confere).

WITH parametros AS (
    SELECT
        CAST(:pedido_distribuicao_ids AS int[]) AS pedido_distribuicao_ids,
        CAST(:id_cliente AS int) AS id_cliente,
        CAST(:id_vendedor AS int) AS id_vendedor,
        CAST(:id_forma_pagamento AS text) AS id_forma_pagamento
),

-- Cabeçalho: 1 linha por item comercial entregue a uma unidade/turma.
distribuicoes AS (
    SELECT
        dm.id AS distribuicao_id,
        ue.id AS unidade_id,
        ue.cliente_id AS cliente_id_unidade,
        ue.nome AS nome_unidade,
        dm.formulario_id,
        dm.pedido_item_carrinho_id,
        dm.quantidade,
        dm.id_turma,
        t.nome AS nome_turma,
        t.area AS area_turma
    FROM pedido_distribuicoes dm
    CROSS JOIN parametros p
    LEFT JOIN escola_unidades ue ON ue.id = dm.unidade_escolar_id
    LEFT JOIN escola_turmas t ON t.id = dm.id_turma
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

-- Agrega os metadados do item comercial a partir dos seus materiais.
-- Uma distribuição = um item no orçamento.
itens_produto AS (
    SELECT
        d.distribuicao_id,
        d.unidade_id,
        d.cliente_id_unidade,
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
        ) AS arquivo_nome_raw
    FROM distribuicoes d
    JOIN materiais mat ON mat.distribuicao_id = d.distribuicao_id
    JOIN pedido_formularios form ON form.id = d.formulario_id
    LEFT JOIN usuarios u ON u.id = form.usuario_id
    GROUP BY
        d.distribuicao_id, d.unidade_id, d.cliente_id_unidade, d.nome_unidade, d.formulario_id,
        d.pedido_item_carrinho_id, d.quantidade, d.id_turma, d.nome_turma, d.area_turma
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
                    'codigo_externo', ip.distribuicao_id::text,
                    'obs_producao', CASE
                        WHEN ip.cliente_id_unidade = 151 THEN
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
                            -- Ordem estável: componentes que não são miolo antes do miolo.
                            ORDER BY mat.is_miolo, mat.id_componente, mat.arquivo_pdf_id
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
                                ORDER BY bp.id_pergunta
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
                                'id', tg.id_tarefa,
                                'descricao', tg.descricao
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
)) AS payload
FROM itens_produto ip
CROSS JOIN parametros p
GROUP BY ip.unidade_id, p.id_cliente, p.id_vendedor, p.id_forma_pagamento
ORDER BY ip.unidade_id;
