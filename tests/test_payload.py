import unittest
from types import SimpleNamespace

from deskflow2.repositorios.fila import OrcamentoReivindicado
from deskflow2.servicos.payload import (
    PayloadIncompleto,
    ids_do_codigo_externo,
    montar_payload_orcamento,
    remover_nulos,
)


def orcamento(**campos):
    base = dict(
        id=700, requisicao_id=300, lote_id=5, modo_envio="assincrono", url_webhook="https://pf/x",
        modo_agrupamento="escola", cliente_id=501, vendedor_id=7, forma_pagamento=11,
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

    def test_mais_de_um_orcamento_no_sql_bloqueia(self):
        conn = ConexaoFalsa([corpo_sql("1,2"), corpo_sql("3")])
        with self.assertRaises(PayloadIncompleto):
            montar_payload_orcamento(conn, orcamento(), "PageFlow")

    def test_sem_forma_de_pagamento_bloqueia_antes_do_sql(self):
        conn = ConexaoFalsa([corpo_sql("1,2,3")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento(forma_pagamento=None), "PageFlow")
        self.assertIn("forma de pagamento", str(ctx.exception))
        self.assertIsNone(conn.parametros)

    def test_modo_desconhecido_bloqueia(self):
        with self.assertRaises(PayloadIncompleto):
            montar_payload_orcamento(ConexaoFalsa([]), orcamento(modo_agrupamento="turma"), "PageFlow")


if __name__ == "__main__":
    unittest.main()
