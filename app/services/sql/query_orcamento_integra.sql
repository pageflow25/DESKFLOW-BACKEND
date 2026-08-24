WITH perguntas_componente AS (
    SELECT
        cbmr.modelo_componente_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id_pergunta', bp.id_pergunta,
                    'pergunta', bp.nome,
                    'tipo', bp.tipo,
                    'resposta', COALESCE(
                        cbmr.valor_texto,
                        br.descricao_opcao,
                        br.valor
                    )
                )
            )
            ORDER BY bp.id_pergunta
        ) AS perguntas
    FROM catalogo_bremen_modelo_respostas cbmr
    JOIN bremen_perguntas bp
        ON bp.id = cbmr.pergunta_id
    LEFT JOIN bremen_respostas br
        ON br.id = cbmr.resposta_id
    WHERE cbmr.modelo_componente_id IS NOT NULL
    GROUP BY cbmr.modelo_componente_id
),

tarefas_componente AS (
    SELECT
        cbmt.modelo_componente_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id', bt.id_tarefa,
                    'descricao', bt.descricao,
                    'descricao_pf', bt.descricao_pf
                )
            )
            ORDER BY cbmt.ordem
        ) AS tarefas
    FROM catalogo_bremen_modelo_tarefas cbmt
    JOIN bremen_tarefas bt
        ON bt.id = cbmt.tarefa_id
    WHERE cbmt.modelo_componente_id IS NOT NULL
    GROUP BY cbmt.modelo_componente_id
),

componentes AS (
    SELECT
        cbm.id AS modelo_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id', cbmc.id_componente,
                    'descricao', bc.descricao,
                    'altura', cbmc.altura_padrao,
                    'largura', cbmc.largura_padrao,
                    'corfrente', cbmc.corfrente,
                    'corverso', cbmc.corverso,
                    'perguntas_componente', COALESCE(pc.perguntas, '[]'::json),
                    'tarefas_componente', COALESCE(tc.tarefas, '[]'::json)
                )
            )
            ORDER BY cbmc.ordem
        ) AS componentes
    FROM catalogo_bremen_modelos cbm
    JOIN catalogo_bremen_modelo_componentes cbmc
        ON cbmc.modelo_id = cbm.id
       AND cbmc.ativo = TRUE
    LEFT JOIN bremen_componentes bc
        ON bc.id_componente = cbmc.id_componente
    LEFT JOIN perguntas_componente pc
        ON pc.modelo_componente_id = cbmc.id
    LEFT JOIN tarefas_componente tc
        ON tc.modelo_componente_id = cbmc.id
    GROUP BY cbm.id
),

perguntas_gerais AS (
    SELECT
        cbmr.modelo_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id_pergunta', bp.id_pergunta,
                    'pergunta', bp.nome,
                    'tipo', bp.tipo,
                    'resposta', COALESCE(
                        cbmr.valor_texto,
                        br.descricao_opcao,
                        br.valor
                    )
                )
            )
            ORDER BY bp.id_pergunta
        ) AS perguntas
    FROM catalogo_bremen_modelo_respostas cbmr
    JOIN bremen_perguntas bp
        ON bp.id = cbmr.pergunta_id
    LEFT JOIN bremen_respostas br
        ON br.id = cbmr.resposta_id
    WHERE cbmr.modelo_componente_id IS NULL
    GROUP BY cbmr.modelo_id
),

tarefas_gerais AS (
    SELECT
        cbmt.modelo_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id', bt.id_tarefa,
                    'descricao', bt.descricao,
                    'descricao_pf', bt.descricao_pf
                )
            )
            ORDER BY cbmt.ordem
        ) AS tarefas
    FROM catalogo_bremen_modelo_tarefas cbmt
    JOIN bremen_tarefas bt
        ON bt.id = cbmt.tarefa_id
    WHERE cbmt.modelo_componente_id IS NULL
    GROUP BY cbmt.modelo_id
),

itens AS (
    SELECT
        ip.id AS pedido_id,
        ipp.id AS pedido_produto_id,
        json_strip_nulls(
            json_build_object(
                'pedido_id', ip.id,
                'pedido_produto_id', ipp.id,
                'id_produto', cbm.id_produto,
                'titulo', cbm.nome || ' | ' || ipp.nome,
                'usar_listapreco', 1,
                'manter_estrutura_mod_produto', 1,
                'quantidade', ipp.quantidade,
                'obs_producao', ip.descricao,
                'arquivo_pdf_quantidade_paginas', ipp.arquivo_pdf_quantidade_paginas,
                'componentes', COALESCE(c.componentes, '[]'::json),
                'perguntas_gerais', COALESCE(pg.perguntas, '[]'::json),
                'tarefas_gerais', COALESCE(tg.tarefas, '[]'::json)
            )
        ) AS item_json
    FROM integra_pedidos ip
    JOIN integra_pedido_produtos ipp
        ON ipp.pedido_id = ip.id
    JOIN integra_status_pedidos isp
        ON isp.id = ip.status_id
    JOIN catalogo_bremen_modelos cbm
        ON cbm.id = ipp.catalogo_bremen_modelo_id
    LEFT JOIN componentes c
        ON c.modelo_id = cbm.id
    LEFT JOIN perguntas_gerais pg
        ON pg.modelo_id = cbm.id
    LEFT JOIN tarefas_gerais tg
        ON tg.modelo_id = cbm.id
    WHERE ip.integracao_id = 2
      AND LOWER(isp.nome) = LOWER('Pedido recebido')
      AND isp.ativo = TRUE
      AND ip.id IN :pedido_ids
)

SELECT json_strip_nulls(
    json_build_object(
        'identifier', 'PageFlow',
        'data',
        json_strip_nulls(
            json_build_object(
                'id_cliente', '3366',
                'id_vendedor', '2284',
                'id_forma_pagamento', '1',
                'pedido_ids', (
                    SELECT json_agg(DISTINCT pedido_id)
                    FROM itens
                ),
                'itens', (
                    SELECT json_agg(item_json ORDER BY pedido_produto_id)
                    FROM itens
                )
            )
        )
    )
) AS pedido_json;
