-- Aprovação de UM orçamento do PCP (POST /api/v1/proposta/aprovar).
--
-- Monta o `data` do corpo — { id_orcamento, gerar_op, itens[] } — para UMA
-- orcamento_api_aprovacoes. O `identifier` e o modo assíncrono
-- (`url_webhook`) são postos pelo handler (handlers/pcp/aprovacao_enviar.py),
-- como no orçamento.
--
-- Antes este JSON vinha pronto no payload do item da fila
-- (`itens_aprovados`), montado pelo PageFlow em
-- services/Pcp/pcpItemRetornoService.js::analisarItensAprovacao +
-- validators/pcpRetornoValidator.js::montarItensAprovados +
-- Fiscal/validators/entregasValidator.js::agregarEntregasDoItem. Este SQL
-- reproduz aquela montagem para que ela possa ser ajustada aqui, junto dos
-- SQLs de orçamento. O PageFlow continua gravando `itens_aprovados` na
-- aprovação (tela/auditoria) e continua BLOQUEANDO a aprovação antes de
-- enfileirar (data de saída vencida, entregas que não fecham com o item) —
-- este SQL só monta, não valida.
--
-- Itens: os devolvidos pelo ERP no retorno do orçamento
-- (`orcamento_api_orcamentos.resposta_api -> data -> itens`), na ordem em que
-- vieram — a mesma fonte que o PageFlow usa (`orcamento.resposta_api.data.itens`
-- na aprovação manual, `retorno.itens` na automática, que é o mesmo corpo).
--
-- `data_entrega` de cada item ('YYYY-MM-DD' + 'T18:00:00.000-03:00'):
--   1. `orcamento_api_orcamentos.data_saida` — a data de SAÍDA escolhida no
--      "Enviar", nas duas origens (igual para todos os itens);
--   2. fallback de lote de escola anterior a 2026-09-24 (orçamento sem
--      data_saida): MIN(pedido_distribuicoes.data_saida) dos pedidos do item;
--   3. por último MIN(pedido_formularios.data_entrega).
--   Item sem nenhuma das três vai só com { id }.
--
-- `entregas[]` (só em lote com `orcamento_api_lotes.entregas_fiscais`): as
-- `pedido_distribuicao_entregas` dos pedidos do item, agrupadas por papel
-- (venda = movimenta_estoque false, remessa = true) e por cliente, endereço,
-- contato, natureza, transportadora, data e valor unitário, somando a
-- quantidade congelada no envio. Venda primeiro, depois remessa, cada papel na
-- ordem em que apareceu (pedido, movimenta_estoque). Datas: venda =
-- orçamento.data_entrega (fallback formulário); remessa = orçamento.data_saida
-- (fallback distribuição). Entrega sem data é descartada (o PageFlow bloqueia
-- a aprovação nesse caso, então aqui ela não deveria chegar).
--
-- Parâmetro: :aprovacao_id (orcamento_api_aprovacoes.id). Devolve 1 linha com
-- o `data` (json), ou nenhuma se a aprovação não existir.

WITH aprovacao AS (
    SELECT
        a.id AS aprovacao_id,
        a.gerar_op,
        o.id AS orcamento_id,
        o.id_orcamento,
        o.resposta_api,
        o.data_saida AS orcamento_data_saida,
        o.data_entrega AS orcamento_data_entrega,
        COALESCE(l.entregas_fiscais, false) AS entregas_fiscais
    FROM orcamento_api_aprovacoes a
    JOIN orcamento_api_orcamentos o
        ON o.id = a.orcamento_api_orcamento_id
    LEFT JOIN orcamento_api_requisicoes r
        ON r.id = o.orcamento_api_requisicao_id
    LEFT JOIN orcamento_api_lotes l
        ON l.id = r.orcamento_api_lote_id
    WHERE a.id = CAST(:aprovacao_id AS int)
),

-- Itens devolvidos pelo ERP. `id` pode vir número ou texto; só entra o que é
-- inteiro (igual ao `Number.isInteger` do PageFlow).
itens_erp AS (
    SELECT
        CAST(item ->> 'id' AS int) AS id_item_orcamento,
        ordem
    FROM aprovacao ap
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(ap.resposta_api -> 'data' -> 'itens') = 'array'
                THEN ap.resposta_api -> 'data' -> 'itens'
            ELSE '[]'::jsonb
        END
    ) WITH ORDINALITY AS e(item, ordem)
    WHERE (item ->> 'id') ~ '^[0-9]+$'
),

-- Data do item a partir do vínculo retorno -> pedido (escola) ou -> produto
-- (integração). Na integração não há pedido_distribuicoes: vale a do orçamento.
datas_item AS (
    SELECT
        ir.id_item_orcamento,
        COALESCE(
            MIN(ap.orcamento_data_saida)::date,
            MIN(pd.data_saida)::date,
            MIN(f.data_entrega)::date
        ) AS dia
    FROM orcamento_api_itens_retorno ir
    JOIN aprovacao ap
        ON ap.orcamento_id = ir.orcamento_api_orcamento_id
    LEFT JOIN pedido_distribuicoes pd
        ON pd.id = ir.pedido_distribuicao_id
    LEFT JOIN pedido_formularios f
        ON f.id = pd.formulario_id
    WHERE ir.id_item_orcamento IS NOT NULL
    GROUP BY ir.id_item_orcamento
),

-- Uma linha por entrega gravada de cada pedido do item (lote com entregas).
linhas_entrega AS (
    SELECT
        ir.id_item_orcamento,
        e.movimenta_estoque,
        e.id_cliente,
        e.id_endereco,
        e.id_contato,
        e.id_nat_operacao,
        e.id_transportadora,
        e.valor_unitario,
        COALESCE(i.quantidade_no_envio, pd.quantidade) AS quantidade,
        CASE
            WHEN e.movimenta_estoque
                THEN COALESCE(ap.orcamento_data_saida::date, pd.data_saida::date)
            ELSE COALESCE(ap.orcamento_data_entrega::date, f.data_entrega::date)
        END AS dia,
        ROW_NUMBER() OVER (
            PARTITION BY ir.id_item_orcamento
            ORDER BY pd.id, e.movimenta_estoque
        ) AS ordem
    FROM aprovacao ap
    JOIN orcamento_api_itens_retorno ir
        ON ir.orcamento_api_orcamento_id = ap.orcamento_id
    JOIN pedido_distribuicoes pd
        ON pd.id = ir.pedido_distribuicao_id
    LEFT JOIN pedido_formularios f
        ON f.id = pd.formulario_id
    LEFT JOIN orcamento_api_itens i
        ON i.id = ir.orcamento_api_item_id
    JOIN pedido_distribuicao_entregas e
        ON e.pedido_distribuicao_id = pd.id
    WHERE ap.entregas_fiscais
      AND ir.id_item_orcamento IS NOT NULL
      AND e.movimenta_estoque IS NOT NULL
),

entregas_agrupadas AS (
    SELECT
        id_item_orcamento,
        movimenta_estoque,
        MIN(ordem) AS ordem,
        json_strip_nulls(
            json_build_object(
                'data_entrega', to_char(dia, 'YYYY-MM-DD'),
                'quantidade', SUM(quantidade),
                'id_cliente', id_cliente,
                'id_endereco', id_endereco,
                'id_contato', id_contato,
                'id_nat_operacao', id_nat_operacao,
                'id_transportadora', id_transportadora,
                'valor_unitario', valor_unitario
            )
        ) AS entrega
    FROM linhas_entrega
    WHERE dia IS NOT NULL
    GROUP BY id_item_orcamento, movimenta_estoque, id_cliente, id_endereco, id_contato,
             id_nat_operacao, id_transportadora, dia, valor_unitario
),

entregas_item AS (
    SELECT
        id_item_orcamento,
        -- Venda (false) antes de remessa (true); dentro do papel, ordem de chegada.
        json_agg(entrega ORDER BY movimenta_estoque, ordem) AS entregas
    FROM entregas_agrupadas
    GROUP BY id_item_orcamento
),

itens AS (
    SELECT
        json_agg(
            json_strip_nulls(
                json_build_object(
                    'id', ie.id_item_orcamento,
                    'data_entrega', to_char(di.dia, 'YYYY-MM-DD'),
                    'entregas', en.entregas
                )
            )
            ORDER BY ie.ordem
        ) AS itens
    FROM itens_erp ie
    LEFT JOIN datas_item di
        ON di.id_item_orcamento = ie.id_item_orcamento
    LEFT JOIN entregas_item en
        ON en.id_item_orcamento = ie.id_item_orcamento
)

SELECT
    json_build_object(
        'id_orcamento', ap.id_orcamento,
        'gerar_op', COALESCE(ap.gerar_op, false),
        'itens', COALESCE(it.itens, '[]'::json)
    ) AS data
FROM aprovacao ap
CROSS JOIN itens it;
