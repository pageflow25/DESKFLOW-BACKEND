import json
import unittest
from types import SimpleNamespace

from app.repositorios.pcp import OrcamentoReivindicado
from app.servicos.pcp import payload as modulo_payload
from app.servicos.pcp.payload import (
    ARQUIVO_AGRUPADO,
    PayloadIncompleto,
    ids_do_codigo_externo,
    montar_payload_orcamento,
)
from app.utils.conversao import remover_nulos


def orcamento(**campos):
    base = dict(
        id=700, requisicao_id=300, lote_id=5, modo_envio="assincrono", url_webhook="https://pf/x",
        cliente_id=501, vendedor_id=7, forma_pagamento=11,
        pedido_distribuicao_ids=[1, 2, 3],
    )
    base.update(campos)
    return OrcamentoReivindicado(**base)


class ConexaoFalsa:
    def __init__(self, linhas):
        self.linhas = linhas
        self.parametros = None

    def execute(self, _consulta, parametros):
        self.parametros = parametros
        linhas = self.linhas
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: linhas))


def corpo_sql(*codigos):
    return {"data": {
        "id_cliente": 501, "id_vendedor": 7, "id_forma_pagamento": "11",
        "itens": [{"id_produto": 62, "codigo_externo": c, "quantidade": 10, "obs": None} for c in codigos],
    }}


class TestPayload(unittest.TestCase):
    def test_ids_do_codigo_externo_aceita_lista_com_virgula(self):
        self.assertEqual(ids_do_codigo_externo("12, 13,x,14"), {12, 13, 14})
        self.assertEqual(ids_do_codigo_externo(None), set())

    def test_remover_nulos_em_qualquer_nivel_mantendo_listas(self):
        self.assertEqual(
            remover_nulos({"a": None, "b": [{"c": None, "d": 1}], "e": {"f": None}}),
            {"b": [{"d": 1}], "e": {}},
        )

    def test_monta_com_identifier_e_codigo_externo_agrupado(self):
        conn = ConexaoFalsa([corpo_sql("1,2", "3")])

        corpo = montar_payload_orcamento(conn, orcamento(), "PageFlow")

        self.assertEqual(corpo["identifier"], "PageFlow")
        self.assertEqual(len(corpo["data"]["itens"]), 2)
        self.assertNotIn("obs", corpo["data"]["itens"][0])
        self.assertEqual(conn.parametros["pedido_distribuicao_ids"], [1, 2, 3])
        self.assertEqual(conn.parametros["id_forma_pagamento"], "11")

    def test_pedido_descartado_pelo_sql_bloqueia_o_envio(self):
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(ConexaoFalsa([corpo_sql("1", "2")]), orcamento(), "PageFlow")
        self.assertIn("[3]", str(ctx.exception))

    def test_pedido_de_outro_orcamento_bloqueia_o_envio(self):
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(ConexaoFalsa([corpo_sql("1,2,3,9")]), orcamento(), "PageFlow")
        self.assertIn("[9]", str(ctx.exception))

    def test_sql_que_devolve_mais_de_um_orcamento_bloqueia_o_envio(self):
        # Desde 2026-10-20 o SQL Agrupado devolve UMA linha com todas as turmas;
        # mais de uma é divergência entre o PageFlow e o SQL.
        conn = ConexaoFalsa([corpo_sql("1,2"), corpo_sql("3")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento(), "PageFlow")
        self.assertIn("2 orçamentos", str(ctx.exception))

    def test_sem_forma_de_pagamento_bloqueia_antes_do_sql(self):
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento(forma_pagamento=None), "PageFlow")
        self.assertIn("forma de pagamento", str(ctx.exception))
        self.assertIsNone(conn.parametros)



class TestObsProducaoJsonNoSqlAgrupado(unittest.TestCase):
    """O `obs_producao` do item de escola é um JSON em texto (2026-11-23).

    Uma chave por tabela de origem, só com os dados do próprio item. Vai como
    texto porque o Wingraph declara o campo como string. Que as tabelas e
    colunas existem é conferido pelo PREPARE de tests/integracao.
    """

    def _corpo(self):
        sql = (modulo_payload.PASTA_SQL / ARQUIVO_AGRUPADO).read_text(encoding="utf-8-sig")
        # Só o SQL de verdade: comentário citando a coluna daria falso positivo.
        return chr(10).join(
            linha for linha in sql.splitlines() if not linha.lstrip().startswith("--")
        )

    def test_o_obs_producao_vai_como_texto_json(self):
        corpo = self._corpo()
        self.assertIn("))::text AS obs_producao", corpo)
        self.assertIn("'obs_producao', ip.obs_producao,", corpo)

    def test_o_json_tem_uma_chave_por_tabela_de_origem(self):
        corpo = self._corpo()
        for chave in ("'pedido_formularios'", "'escola_unidades'", "'escola_turmas'", "'pedido_arquivos_pdf'"):
            self.assertIn(chave, corpo)
        for campo in ("'id', form.id", "'nome', form.nome", "'email', form.email", "'criado_em', form.criado_em",
                      "'titulo', form.titulo", "'area', t.area", "'arquivo', ap.arquivo"):
            self.assertIn(campo, corpo)

    def test_unidade_e_objeto_quando_unica_e_lista_quando_varias(self):
        self.assertIn(
            "CASE WHEN ui.quantidade_unidades = 1 THEN ui.unidades -> 0 ELSE ui.unidades END",
            self._corpo(),
        )

    def test_o_texto_antigo_saiu(self):
        corpo = self._corpo()
        self.assertNotIn("Data de Entrega", corpo)
        self.assertNotIn(":data_entrega", corpo)
        self.assertNotIn("form.observacoes", corpo)

    def test_a_data_continua_indo_ao_sql(self):
        # Os parâmetros são os mesmos nas duas origens; só o SQL de integração
        # ainda usa a data, e o SQLAlchemy ignora parâmetro que o SQL não declara.
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        montar_payload_orcamento(conn, orcamento(data_entrega="01/12/2026"), "PageFlow")
        self.assertEqual(conn.parametros["data_entrega"], "01/12/2026")


class TestComplementoDoEnvioParcial(unittest.TestCase):
    """O complemento do nome dos arquivos de um envio PARCIAL (2026-10-21).

    O PageFlow decide quais pedidos são de formulário enviado só em parte e
    manda `{id: texto}` no payload; o SQL Agrupado acrescenta o texto ao título
    do item. Aqui se trava a ponte: o mapa vira o parâmetro JSON do SQL, e o SQL
    o declara e o usa no título.
    """

    def _corpo(self):
        sql = (modulo_payload.PASTA_SQL / ARQUIVO_AGRUPADO).read_text(encoding="utf-8-sig")
        return chr(10).join(
            linha for linha in sql.splitlines() if not linha.lstrip().startswith("--")
        )

    def test_o_mapa_vai_ao_sql_como_json_com_ids_em_texto(self):
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        montar_payload_orcamento(
            conn, orcamento(complementos_nome_arquivo={1: "2ª remessa", 2: "2ª remessa"}), "PageFlow")
        self.assertEqual(
            json.loads(conn.parametros["complementos_nome_arquivo"]),
            {"1": "2ª remessa", "2": "2ª remessa"},
        )
        self.assertIn("2ª remessa", conn.parametros["complementos_nome_arquivo"])

    def test_sem_complemento_o_parametro_vai_nulo(self):
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        montar_payload_orcamento(conn, orcamento(), "PageFlow")
        self.assertIn("complementos_nome_arquivo", conn.parametros)
        self.assertIsNone(conn.parametros["complementos_nome_arquivo"])

    def test_preco_fechado_vai_ao_sql_como_json_de_texto_decimal(self):
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        montar_payload_orcamento(
            conn, orcamento(valores_fechados_unitario={1: "12.3450", 3: "0.5000"}), "PageFlow")
        self.assertEqual(
            json.loads(conn.parametros["valores_fechados_unitario"]),
            {"1": "12.3450", "3": "0.5000"},
        )

    def test_sem_preco_fechado_o_parametro_vai_nulo(self):
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        montar_payload_orcamento(conn, orcamento(), "PageFlow")
        self.assertIn("valores_fechados_unitario", conn.parametros)
        self.assertIsNone(conn.parametros["valores_fechados_unitario"])

    def test_o_sql_le_o_preco_fechado_do_parametro_e_nao_decide_mais(self):
        corpo = self._corpo()
        self.assertIn("CAST(:valores_fechados_unitario AS jsonb)", corpo)
        self.assertIn("'usar_listapreco', CASE WHEN ip.valor_total_fechado IS NULL THEN 1 ELSE 0 END", corpo)
        # A regra (formulário do "Fechar pedido", componentes) é do PageFlow.
        self.assertNotIn("precificacao_pedido_item_valores", corpo)
        self.assertNotIn("formulario_orcamento_id", corpo)

    def test_o_sql_declara_o_parametro_e_o_poe_no_titulo(self):
        corpo = self._corpo()
        self.assertIn("CAST(:complementos_nome_arquivo AS jsonb)", corpo)
        # Montado uma vez e usado nas duas formas de título (com e sem turma) e
        # no nome de reserva.
        self.assertIn("COALESCE(' - ' || i.complemento_nome_arquivo, '') AS sufixo", corpo)
        self.assertEqual(corpo.count("|| s.sufixo"), 3)


if __name__ == "__main__":
    unittest.main()
