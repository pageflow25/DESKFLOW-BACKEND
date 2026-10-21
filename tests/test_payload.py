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



class TestDataDeEntregaNoSqlAgrupado(unittest.TestCase):
    """A data escolhida no \"Enviar\" manda no obs_producao também na escola.

    Desde 2026-09-24 o modal da cascata pede as duas datas e o PageFlow as
    grava em orcamento_api_orcamentos. O SQL de escola (o Agrupado) a recebe
    em :data_entrega e a prefere à do formulário — com COALESCE, para os
    lotes que já estavam na fila (coluna nula) seguirem montando igual.
    """

    def _corpo(self, arquivo):
        sql = (modulo_payload.PASTA_SQL / arquivo).read_text(encoding="utf-8-sig")
        # Só o SQL de verdade: comentário citando a coluna daria falso positivo.
        return chr(10).join(
            linha for linha in sql.splitlines() if not linha.lstrip().startswith("--")
        )

    def test_o_sql_declara_e_prefere_a_data_do_orcamento(self):
        corpo = self._corpo(ARQUIVO_AGRUPADO)
        self.assertIn(":data_entrega", corpo)
        self.assertIn(
            "'Data de Entrega: ' || COALESCE(p.data_entrega, ip.data_entrega_pedido, '-')",
            corpo,
        )

    def test_a_data_vai_ao_sql(self):
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

    def test_o_sql_declara_o_parametro_e_o_poe_no_titulo(self):
        corpo = self._corpo()
        self.assertIn("CAST(:complementos_nome_arquivo AS jsonb)", corpo)
        # Nas duas formas de título (com e sem turma) e no nome de reserva.
        self.assertEqual(corpo.count("COALESCE(' - ' || ia.complemento_nome_arquivo, '')"), 3)


if __name__ == "__main__":
    unittest.main()
