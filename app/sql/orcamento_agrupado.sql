-- database: :memory:
-- SQL Agrupado: o ÚNICO SQL de orçamento dos lotes de ESCOLA
-- (orcamento_api_lotes.origem = 'escola').
--
-- Monta o corpo de UM POST /api/v1/orcamento para UM orcamento_api_orcamentos
-- do PCP, que o PageFlow cria com todos os pedidos de um mesmo cliente/
-- vendedor/forma de pagamento, de todas as turmas. Dentro dele, 1 item por item
-- de carrinho E turma: soma as quantidades das unidades que pedem o mesmo item
-- na mesma turma, e o item de turma leva o nome dela na frente do título.
-- Itens sem turma vêm primeiro, depois as turmas pela ordem do nome.
--
-- O que vem PRONTO do PageFlow (aqui só se lê, não se decide):
--   - uma especificação por componente, com papel em três eixos e cor já
--     resolvidos — inclusive a dos componentes SEM arquivo (papelão, guarda),
--     marcados com `metadados.sem_arquivo = true`;
--   - o nome do arquivo formatado (pedido_arquivos_pdf.nome_formatado);
--   - o complemento do título nos envios PARCIAIS (:complementos_nome_arquivo);
--   - o preço fechado da Calculadora (precificacao_pedido_item_valores).
-- O histórico de cada regra está no git log deste arquivo.
--
-- Parâmetros: :pedido_distribuicao_ids (int[]), :id_cliente, :id_vendedor,
-- :id_forma_pagamento, :data_entrega (texto 'DD/MM/YYYY' do orçamento, vindo do
-- claim — ver repositorios/fila.py; NULL nos lotes anteriores a 2026-09-24, que
-- caem na data do formulário), :complementos_nome_arquivo (texto JSON
-- {"<id do pedido>": "complemento"}; NULL sem envio parcial). Devolve 1 linha
-- (o orçamento inteiro) ou nenhuma, quando nenhum item pôde ser montado.

WITH parametros AS (
    SELECT
        CAST(:pedido_distribuicao_ids AS int[]) AS pedido_distribuicao_ids,
        CAST(:id_cliente AS int) AS id_cliente,
        CAST(:id_vendedor AS int) AS id_vendedor,
        CAST(:id_forma_pagamento AS text) AS id_forma_pagamento,
        CAST(:data_entrega AS text) AS data_entrega,
        CAST(:complementos_nome_arquivo AS jsonb) AS complementos_nome_arquivo
),

-- 1 linha por pedido: um item de carrinho entregue a uma unidade/turma.
distribuicoes AS (
    SELECT
        dm.id AS distribuicao_id,
        dm.formulario_id,
        dm.pedido_item_carrinho_id,
        dm.quantidade,
        dm.id_turma
    FROM pedido_distribuicoes dm
    CROSS JOIN parametros p
    WHERE dm.id = ANY(p.pedido_distribuicao_ids)
      AND dm.quantidade > 0
),

-- 1 linha por item do orçamento (item de carrinho × turma). Todos os pedidos do
-- grupo têm os mesmos componentes, então os do primeiro (a referência)
-- descrevem o item inteiro.
itens AS (
    SELECT
        d.pedido_item_carrinho_id,
        d.id_turma,
        MAX(d.formulario_id) AS formulario_id,
        MIN(d.distribuicao_id) AS distribuicao_referencia,
        SUM(d.quantidade) AS quantidade_total,
        array_to_string(array_agg(DISTINCT d.distribuicao_id ORDER BY d.distribuicao_id), ',') AS codigo_externo,
        -- Um item de carrinho é de um formulário só: ou o grupo inteiro é do
        -- envio parcial, ou nenhum pedido dele é.
        UPPER(MAX(NULLIF(TRIM(p.complementos_nome_arquivo ->> d.distribuicao_id::text), ''))) AS complemento_nome_arquivo
    FROM distribuicoes d
    CROSS JOIN parametros p
    GROUP BY d.pedido_item_carrinho_id, d.id_turma
),

-- Componentes do pedido de referência de cada item, de duas fontes:
--   1. os arquivos distribuídos (capa, miolo, folha de rosto);
--   2. as especificações SEM arquivo do mesmo item de carrinho (papelão,
--      guarda). O critério é a marca `sem_arquivo`, não "não tem arquivo":
--      pedidos de jun–ago têm especificações repetidas no carrinho que nunca
--      foram componente. O CASE só converte para jsonb o texto que contém a
--      marca, para um `metadados` malformado não derrubar o orçamento.
componentes AS (
    SELECT
        pda.distribuicao_material_id AS distribuicao_id,
        pda.especificacao_form_id,
        pda.arquivo_pdf_id,
        pda.id_componente
    FROM pedido_distribuicao_arquivos pda
    WHERE pda.distribuicao_material_id IN (SELECT distribuicao_referencia FROM itens)

    UNION ALL

    SELECT i.distribuicao_referencia, ef.id, NULL, ef.id_componente
    FROM itens i
    JOIN pedido_especificacoes ef ON ef.pedido_item_carrinho_id = i.pedido_item_carrinho_id
    WHERE CASE
              WHEN ef.metadados LIKE '%sem_arquivo%'
              THEN (ef.metadados::jsonb ->> 'sem_arquivo') = 'true'
              ELSE FALSE
          END
),

-- 1 linha por (pedido de referência, componente), já nos valores que vão ao
-- ERP. Se o componente vier pelas duas fontes, fica a que tem arquivo.
materiais AS (
    SELECT DISTINCT ON (c.distribuicao_id, COALESCE(c.id_componente, ef.id_componente))
        c.distribuicao_id,
        c.arquivo_pdf_id,
        c.especificacao_form_id,
        COALESCE(c.id_componente, ef.id_componente) AS id_componente,
        ef.id_produto,
        -- Posição do componente no item (Capa, Miolo 1, Miolo 2…); nula em
        -- pedidos anteriores a 2026-10-20.
        ef.ordem_componente,
        COALESCE(bc.is_miolo, FALSE) AS is_miolo,
        bc.descricao AS componente_descricao,
        ap.nome_formatado AS arquivo_nome_formatado,
        ap.paginas,
        ef.id_substrato AS idgruposubstratoimpressao,
        -- Papel em três eixos; pedido antigo sem eles cai no texto livre da
        -- especificação (`gramatura_miolo` é a gramatura DO componente, apesar
        -- do nome).
        COALESCE(
            bg.gramatura,
            NULLIF(replace(regexp_replace(ef.gramatura_miolo::text, '[^0-9.,]', '', 'g'), ',', '.'), '')::numeric
        ) AS gramatura,
        ROUND(COALESCE(bf.altura, NULLIF(ef.altura, '')::numeric) / 10, 2) AS altura_cm,
        ROUND(COALESCE(bf.largura, NULLIF(ef.largura, '')::numeric) / 10, 2) AS largura_cm,
        -- A cor da CAPA não vai (a gravada é a do PDF/item: capa colorida de
        -- miolo PB sairia 1/1) e o Bremen usa a do modelo. "Capa + Miolo"
        -- (is_capa E is_miolo) é o PDF inteiro: a cor dele vai.
        CASE WHEN COALESCE(bc.is_miolo, FALSE) OR NOT COALESCE(bc.is_capa, FALSE) THEN ef.corfrente END AS corfrente,
        CASE WHEN COALESCE(bc.is_miolo, FALSE) OR NOT COALESCE(bc.is_capa, FALSE) THEN ef.corverso END AS corverso
    FROM componentes c
    JOIN pedido_especificacoes ef ON ef.id = c.especificacao_form_id
    LEFT JOIN pedido_arquivos_pdf ap ON ap.id = c.arquivo_pdf_id
    LEFT JOIN bremen_componentes bc ON bc.id_componente = COALESCE(c.id_componente, ef.id_componente)
    LEFT JOIN bremen_gramatura bg ON bg.id = ef.id_gramatura
    LEFT JOIN bremen_formato_papel bf ON bf.id = ef.id_formato
    -- Produto fora do catálogo Bremen não monta (payload.py acusa o pedido).
    WHERE EXISTS (SELECT 1 FROM bremen_itens bi WHERE bi.id_produto = ef.id_produto)
    ORDER BY
        c.distribuicao_id,
        COALESCE(c.id_componente, ef.id_componente),
        (c.arquivo_pdf_id IS NULL),
        c.arquivo_pdf_id
),

-- O que o item tira dos seus componentes: o produto, a especificação das
-- perguntas e tarefas GERAIS e o nome do arquivo do título. Os dois últimos vêm
-- do PRIMEIRO miolo pela ordem do item (pedido antigo, sem ordem: pelo id).
resumo_item AS (
    SELECT
        m.distribuicao_id,
        MAX(m.id_produto) AS id_produto,
        COALESCE(
            (ARRAY_AGG(m.especificacao_form_id ORDER BY m.ordem_componente NULLS LAST, m.id_componente)
                FILTER (WHERE m.is_miolo))[1],
            -- Sem miolo, uma com arquivo: papelão/guarda não têm as perguntas gerais.
            MAX(m.especificacao_form_id) FILTER (WHERE m.arquivo_pdf_id IS NOT NULL),
            MAX(m.especificacao_form_id)
        ) AS especificacao_id_geral,
        COALESCE(
            (ARRAY_AGG(m.arquivo_nome_formatado ORDER BY m.ordem_componente NULLS LAST, m.id_componente)
                FILTER (WHERE m.is_miolo AND m.arquivo_nome_formatado IS NOT NULL))[1],
            MAX(m.arquivo_nome_formatado)
        ) AS nome_arquivo
    FROM materiais m
    GROUP BY m.distribuicao_id
),

-- Preço fechado no PageFlow (Calculadora): soma de valor_fechado_unitario ×
-- quantidade de cada pedido × componente. Só para pedido gerado pelo "Fechar
-- pedido" (formulario_orcamento_id): o fechado de tabela que o PageFlow grava
-- em pedido comum não pode mandar sobre a lista do ERP. Item com algum
-- componente sem fechado fica fora e vai pela lista. É o TOTAL da linha, não o
-- unitário (conferido no retorno do ERP em 2026-10-30).
precos_fechados AS (
    SELECT
        d.pedido_item_carrinho_id,
        d.id_turma,
        SUM(v.valor_fechado_unitario * d.quantidade) AS valor_total_fechado
    FROM distribuicoes d
    JOIN pedido_formularios f ON f.id = d.formulario_id
    LEFT JOIN precificacao_pedido_item_valores v ON v.distribuicao_id = d.distribuicao_id
    WHERE f.formulario_orcamento_id IS NOT NULL
    GROUP BY d.pedido_item_carrinho_id, d.id_turma
    HAVING BOOL_AND(v.valor_fechado_unitario IS NOT NULL)
       AND SUM(v.valor_fechado_unitario * d.quantidade) > 0
),

-- O item pronto para o JSON: cabeçalho, título e textos do formulário.
itens_produto AS (
    SELECT
        i.id_turma,
        i.distribuicao_referencia,
        i.quantidade_total,
        i.codigo_externo,
        r.id_produto,
        r.especificacao_id_geral,
        pf.valor_total_fechado,
        t.nome AS nome_turma,
        -- "(#TURMA) - NOME - COMPLEMENTO - (#form)" ou "NOME - COMPLEMENTO (#form)";
        -- sem nome de arquivo, "Produto <id> - COMPLEMENTO".
        COALESCE(
            CASE
                WHEN i.id_turma IS NOT NULL AND NULLIF(TRIM(t.nome), '') IS NOT NULL
                THEN '(#' || TRIM(t.nome) || ') - ' || r.nome_arquivo || s.sufixo || ' - (#' || form.id || ')'
                ELSE r.nome_arquivo || s.sufixo || ' (#' || form.id || ')'
            END,
            'Produto ' || r.id_produto || s.sufixo
        ) AS titulo,
        form.observacoes AS obs_producao,
        TO_CHAR(form.data_entrega, 'DD/MM/YYYY') AS data_entrega_pedido,
        form.titulo AS form_titulo
    FROM itens i
    CROSS JOIN LATERAL (SELECT COALESCE(' - ' || i.complemento_nome_arquivo, '') AS sufixo) s
    JOIN resumo_item r ON r.distribuicao_id = i.distribuicao_referencia
    JOIN pedido_formularios form ON form.id = i.formulario_id
    LEFT JOIN escola_turmas t ON t.id = i.id_turma
    LEFT JOIN precos_fechados pf
        ON pf.pedido_item_carrinho_id = i.pedido_item_carrinho_id
       AND pf.id_turma IS NOT DISTINCT FROM i.id_turma
),

-- Resposta de cada pergunta (de componente ou geral) nas especificações usadas.
respostas AS (
    SELECT DISTINCT ON (pr.especificacao_id, bp.id)
        pr.especificacao_id,
        bp.id_componente,
        bp.id_geral,
        bp.id_pergunta,
        bp.nome AS pergunta,
        bp.tipo,
        br.descricao_opcao AS resposta
    FROM pedido_pergunta_resposta pr
    JOIN bremen_perguntas bp ON bp.id = pr.pergunta_id
    JOIN bremen_respostas br ON br.id = pr.resposta_id
    WHERE br.valor IS NOT NULL
      AND pr.especificacao_id IN (
          SELECT especificacao_form_id FROM materiais
          UNION
          SELECT especificacao_id_geral FROM resumo_item
      )
    ORDER BY pr.especificacao_id, bp.id, pr.id
),

-- Tarefas de cada especificação. Só as EXTRAS (pedidas no formulário) vão ao
-- ERP — o Bremen completa o resto pelo modelo —, a não ser que a tela de
-- Configurações ligue `tarefas.enviar_obrigatorias_erp`; aí as obrigatórias
-- (vínculo item × componente) também vão. Lida a cada envio.
tarefas AS (
    SELECT
        bet.especificacao_id,
        bt.id_tarefa,
        bt.descricao,
        bt.id_componente IS TRUE AS de_componente,
        bt.id_geral IS TRUE AS geral
    FROM pedido_especificacoes_tarefas bet
    JOIN bremen_tarefas bt ON bt.id = bet.tarefa_id
    WHERE (
            bet.origem = 'extra'
            OR EXISTS (
                SELECT 1 FROM configuracao_sistema
                 WHERE chave = 'tarefas.enviar_obrigatorias_erp'
                   AND LOWER(TRIM(valor)) = 'true'
            )
          )
      AND bet.especificacao_id IN (
          SELECT especificacao_form_id FROM materiais
          UNION
          SELECT especificacao_id_geral FROM resumo_item
      )
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
                    'titulo', ip.titulo,
                    'codigo_externo', ip.codigo_externo,
                    'obs_producao', CONCAT_WS(
                        CHR(10) || CHR(10),
                        ip.obs_producao,
                        CONCAT_WS(
                            CHR(10),
                            'Data de Entrega: ' || COALESCE(p.data_entrega, ip.data_entrega_pedido, '-'),
                            'Título: ' || COALESCE(ip.form_titulo, '-')
                        )
                    ),
                    'quantidade', ip.quantidade_total,
                    -- Com preço fechado, vai ele e a lista do ERP não é usada.
                    'usar_listapreco', CASE WHEN ip.valor_total_fechado IS NULL THEN 1 ELSE 0 END,
                    'valor_total_final', ip.valor_total_fechado,
                    'manter_estrutura_mod_produto', 1,
                    -- Um componente, um objeto, da especificação DELE. Chave nula
                    -- some no json_strip_nulls e o Bremen completa pelo modelo
                    -- (`manter_estrutura_mod_produto`) — p.ex. as páginas de
                    -- componente sem arquivo.
                    'componentes', COALESCE((
                        SELECT json_agg(
                            json_build_object(
                                'id', mat.id_componente,
                                'descricao', mat.componente_descricao,
                                'altura', mat.altura_cm,
                                'largura', mat.largura_cm,
                                'quantidade_paginas', mat.paginas,
                                'idgruposubstratoimpressao', mat.idgruposubstratoimpressao,
                                'gramaturasubstratoimpressao', mat.gramatura,
                                'corfrente', mat.corfrente,
                                'corverso', mat.corverso,
                                'perguntas_componente', COALESCE((
                                    SELECT json_agg(
                                        json_build_object(
                                            'id_pergunta', r.id_pergunta,
                                            'pergunta', r.pergunta,
                                            'tipo', r.tipo,
                                            'resposta', r.resposta
                                        )
                                        ORDER BY r.id_pergunta
                                    )
                                    FROM respostas r
                                    WHERE r.especificacao_id = mat.especificacao_form_id
                                      AND r.id_componente = mat.id_componente
                                ), '[]'::json),
                                'tarefas_componente', COALESCE((
                                    SELECT json_agg(json_build_object('id', t.id_tarefa, 'descricao', t.descricao) ORDER BY t.id_tarefa)
                                    FROM (
                                        SELECT DISTINCT id_tarefa, descricao FROM tarefas
                                         WHERE especificacao_id = mat.especificacao_form_id AND de_componente
                                    ) t
                                ), '[]'::json)
                            )
                            -- Na ordem do item (Capa, Miolo 1, Miolo 2…). Pedido
                            -- antigo, sem ordem: o que não é miolo antes do miolo.
                            ORDER BY mat.ordem_componente NULLS LAST, mat.is_miolo, mat.id_componente, mat.arquivo_pdf_id
                        )
                        FROM materiais mat
                        WHERE mat.distribuicao_id = ip.distribuicao_referencia
                    ), '[]'::json),
                    'perguntas_gerais', COALESCE((
                        SELECT json_agg(
                            json_build_object(
                                'tipo', r.tipo,
                                'pergunta', r.pergunta,
                                'resposta', r.resposta,
                                'id_pergunta', r.id_pergunta
                            )
                            ORDER BY r.id_pergunta
                        )
                        FROM respostas r
                        WHERE r.especificacao_id = ip.especificacao_id_geral
                          AND r.id_geral = ip.id_produto
                    ), '[]'::json),
                    'tarefas_gerais', COALESCE((
                        SELECT json_agg(json_build_object('id', t.id_tarefa, 'descricao', t.descricao) ORDER BY t.id_tarefa)
                        FROM (
                            SELECT DISTINCT id_tarefa, descricao FROM tarefas
                             WHERE especificacao_id = ip.especificacao_id_geral AND geral
                        ) t
                    ), '[]'::json)
                )
                -- Sem turma primeiro; depois as turmas pelo nome, cada uma com
                -- os itens juntos.
                ORDER BY ip.id_turma IS NOT NULL, ip.nome_turma, ip.id_turma, ip.distribuicao_referencia
            ), '[]'::json
        )
    )
)) AS payload
FROM itens_produto ip
CROSS JOIN parametros p
GROUP BY p.id_cliente, p.id_vendedor, p.id_forma_pagamento;
