-- Orçamento de pedidos de INTEGRAÇÃO (orcamento_api_lotes.origem = 'integracao').
--
-- Monta o corpo de UM POST /api/v1/orcamento para UM orcamento_api_orcamentos
-- do PCP — que o PageFlow já criou com os produtos de UM integra_pedidos
-- (1 orçamento por pedido do parceiro). Irmão de orcamento_unidade.sql /
-- orcamento_escola.sql, que fazem o mesmo para pedidos de escola.
--
-- Diferenças para os SQLs de escola:
--   - A origem do item é `integra_pedido_produtos`, não `pedido_distribuicoes`.
--     Cada produto do pedido vira UM item do orçamento.
--   - A estrutura (componentes, perguntas, tarefas) vem do CATÁLOGO VIVO
--     (`catalogo_bremen_modelos` e filhas), não da especificação do pedido.
--     `integra_pedido_produtos.catalogo_bremen_modelo_snapshot_id` existe no
--     banco mas está vazio e não é lido aqui: mudar o modelo no catálogo muda
--     o que se manda ao ERP nos envios seguintes. Decisão do usuário (2026-09-22).
--   - Não há três eixos de papel nem gramatura por pedido: o que existe é o
--     que o modelo do catálogo define.
--
-- Base: o SQL manual que o usuário já rodava para estes pedidos, com as
-- correções pedidas:
--   (a) cada item leva `codigo_externo` = integra_pedido_produtos.id::text —
--       é por ele que o PageFlow liga o retorno do ERP a cada produto
--       (services/Pcp/pcpItemRetornoService.js);
--   (b) `pedido_ids` saiu: não faz parte do corpo que o Wingraph aceita;
--   (c) `tarefas_componente` e `tarefas_gerais` saem como { id, descricao },
--       com `bremen_tarefas.descricao` — o mesmo formato e a mesma coluna dos
--       SQLs de escola (a versão manual usava `descricao_pf`, que é o rótulo
--       de tela do PageFlow, e `id_tarefa`/`descricao`/`descricao_pf` no
--       componente);
--   (d) capa e miolo são decididos por `bremen_componentes.is_capa/is_miolo`,
--       não por LIKE na descrição;
--   (e) `altura`/`largura` vão como estão em
--       `catalogo_bremen_modelo_componentes.altura_padrao/largura_padrao`, que
--       já estão em CENTÍMETROS (ao contrário dos SQLs de escola, que leem
--       milímetros de bremen_formato/bremen_tamanho_papel e por isso dividem
--       por 10). Ver o relatório: 86 das 89 linhas do catálogo em testing
--       estão em cm; 3 linhas antigas (210, 230, 146) parecem mm e sairiam
--       10x maiores — corrigir esses 3 cadastros, não o SQL;
--   (f) sem `identifier` e sem cabeçalho fixo: `identifier` é posto pelo
--       payload.py e cliente/vendedor/forma vêm da requisição do PCP.
--
-- `obs_producao` do item: a descrição do pedido do parceiro MAIS a data de
-- entrega do orçamento, no mesmo formato do orcamento_escola.sql
-- ('Data de Entrega: DD/MM/YYYY', separada por linha em branco). Atenção: essa
-- data é INFORMATIVA — a data que vai ao ERP como data do item é a
-- `orcamento_api_orcamentos.data_saida`, e ela vai na APROVAÇÃO, montada pelo
-- PageFlow (services/Pcp/pcpItemRetornoService.js), não aqui.
--
-- Parâmetros: :integra_pedido_produto_ids (int[]), :id_cliente, :id_vendedor,
-- :id_forma_pagamento, :data_entrega (texto 'DD/MM/YYYY' do orçamento, vindo
-- do claim — ver repositorios/fila.py). Devolve 1 linha (o serviço confere).

WITH parametros AS (
    SELECT
        CAST(:integra_pedido_produto_ids AS int[]) AS produto_ids,
        CAST(:id_cliente AS int) AS id_cliente,
        CAST(:id_vendedor AS int) AS id_vendedor,
        CAST(:id_forma_pagamento AS text) AS id_forma_pagamento,
        CAST(:data_entrega AS text) AS data_entrega
),

-- Produtos deste orçamento, já com o pedido e o modelo do catálogo.
produtos AS (
    SELECT
        ipp.id AS produto_id,
        ipp.nome AS produto_nome,
        ipp.quantidade,
        ipp.arquivo_pdf_quantidade_paginas,
        ip.id AS pedido_id,
        ip.numero_pedido,
        -- Descrição do pedido do parceiro + a data de entrega do orçamento,
        -- no formato do orcamento_escola.sql. CONCAT_WS ignora NULL, então
        -- pedido sem descrição sai só com a data e vice-versa.
        CONCAT_WS(
            CHR(10) || CHR(10),
            NULLIF(ip.descricao, ''),
            'Data de Entrega: ' || COALESCE(p.data_entrega, '-')
        ) AS obs_producao,
        cbm.id AS modelo_id,
        cbm.nome AS modelo_nome,
        cbm.id_produto
    FROM integra_pedido_produtos ipp
    CROSS JOIN parametros p
    JOIN integra_pedidos ip
        ON ip.id = ipp.pedido_id
    JOIN catalogo_bremen_modelos cbm
        ON cbm.id = ipp.catalogo_bremen_modelo_id
    WHERE ipp.id = ANY (p.produto_ids)
),

-- Respostas de escopo COMPONENTE do modelo (modelo_componente_id preenchido).
perguntas_componente AS (
    SELECT
        cbmr.modelo_componente_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id_pergunta', bp.id_pergunta,
                    'pergunta', bp.nome,
                    'tipo', bp.tipo,
                    'resposta', COALESCE(cbmr.valor_texto, br.descricao_opcao, br.valor)
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

-- Tarefas de escopo COMPONENTE. Formato { id, descricao }, igual aos SQLs
-- de escola (correção (c)).
tarefas_componente AS (
    SELECT
        cbmt.modelo_componente_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id', bt.id_tarefa,
                    'descricao', bt.descricao
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

-- Respostas de escopo GERAL do modelo (modelo_componente_id nulo).
perguntas_gerais AS (
    SELECT
        cbmr.modelo_id,
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id_pergunta', bp.id_pergunta,
                    'pergunta', bp.nome,
                    'tipo', bp.tipo,
                    'resposta', COALESCE(cbmr.valor_texto, br.descricao_opcao, br.valor)
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
                    'descricao', bt.descricao
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
        pr.produto_id,
        json_strip_nulls(
            json_build_object(
                'id_produto', pr.id_produto,
                'titulo', pr.numero_pedido || ' | ' || pr.modelo_nome || ' | ' || pr.produto_nome,
                -- (a) É por este campo que o retorno do ERP volta a cada produto.
                'codigo_externo', pr.produto_id::text,
                'usar_listapreco', 1,
                'manter_estrutura_mod_produto', 1,
                'quantidade', pr.quantidade,
                'obs_producao', pr.obs_producao,
                'arquivo_pdf_quantidade_paginas', pr.arquivo_pdf_quantidade_paginas,
                'componentes', COALESCE(c.componentes, '[]'::json),
                'perguntas_gerais', COALESCE(pg.perguntas, '[]'::json),
                'tarefas_gerais', COALESCE(tg.tarefas, '[]'::json)
            )
        ) AS item_json
    FROM produtos pr
    LEFT JOIN LATERAL (
        SELECT
            json_agg(
                json_strip_nulls(
                    json_build_object(
                        'id', cbmc.id_componente,
                        'descricao', bc.descricao,
                        -- (e) catálogo já guarda em cm: nada de dividir por 10.
                        'altura', cbmc.altura_padrao,
                        'largura', cbmc.largura_padrao,
                        'corfrente', cbmc.corfrente,
                        'corverso', cbmc.corverso,
                        -- (d) capa/miolo pelas flags do componente Bremen.
                        -- Capa = 2 páginas (frente e verso); miolo = as páginas
                        -- do PDF que o parceiro informou. Componente que não é
                        -- nem um nem outro cai no padrão do modelo (ou nada).
                        'quantidade_paginas',
                            CASE
                                WHEN bc.is_capa THEN 2
                                WHEN bc.is_miolo THEN pr.arquivo_pdf_quantidade_paginas
                                ELSE cbmc.quantidade_paginas_padrao
                            END,
                        'perguntas_componente', COALESCE(pc.perguntas, '[]'::json),
                        'tarefas_componente', COALESCE(tc.tarefas, '[]'::json)
                    )
                )
                -- Miolo antes da capa, como nos SQLs de escola.
                ORDER BY COALESCE(bc.is_capa, FALSE), cbmc.ordem, cbmc.id
            ) AS componentes
        FROM catalogo_bremen_modelo_componentes cbmc
        LEFT JOIN bremen_componentes bc
            ON bc.id_componente = cbmc.id_componente
        LEFT JOIN perguntas_componente pc
            ON pc.modelo_componente_id = cbmc.id
        LEFT JOIN tarefas_componente tc
            ON tc.modelo_componente_id = cbmc.id
        WHERE cbmc.modelo_id = pr.modelo_id
          AND cbmc.ativo = TRUE
    ) c ON TRUE
    LEFT JOIN perguntas_gerais pg
        ON pg.modelo_id = pr.modelo_id
    LEFT JOIN tarefas_gerais tg
        ON tg.modelo_id = pr.modelo_id
)

-- (b) sem `pedido_ids`; (f) sem `identifier` — o payload.py o acrescenta.
SELECT json_strip_nulls(
    json_build_object(
        'data',
        json_build_object(
            'id_cliente', p.id_cliente,
            'id_vendedor', p.id_vendedor,
            'id_forma_pagamento', p.id_forma_pagamento,
            'itens', COALESCE(
                (SELECT json_agg(item_json ORDER BY produto_id) FROM itens),
                '[]'::json
            )
        )
    )
) AS payload
FROM parametros p;
