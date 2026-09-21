import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

from deskflow2.clientes.erp import ErpIndisponivel
from deskflow2.clientes.pageflow import Desfecho, PageflowClient
from deskflow2.servicos import reconciliacao
from deskflow2.servicos.reconciliacao import Reconciliacao, extrair_payload_requisicao
from deskflow2.servicos.repasse import Repassador
from tests.apoio import MotorFalso, RepassadorFalso, configuracao


class ErpConsulta:
    def __init__(self, corpo=None, erro=None):
        self.corpo = corpo
        self.erro = erro

    def get(self, caminho, params=None):
        if self.erro:
            raise self.erro
        return httpx.Response(200, json=self.corpo)


RESULTADO = {"sucess": True, "code": 200, "data": {"id_orcamento": 22521, "itens": []}}
CONSULTA_PROCESSADA = {
    "success": True,
    "data": {"id_requisicao": 40430, "status_processamento": "Processado", "payload": json.dumps(RESULTADO)},
}


class TestReconciliacao(unittest.TestCase):
    def setUp(self):
        self.fila = mock.patch.object(reconciliacao, "fila").start()
        self.fila.TABELA_ORCAMENTOS = "orcamento_api_orcamentos"
        self.fila.TABELA_APROVACOES = "orcamento_api_aprovacoes"
        mock.patch.object(reconciliacao, "carregar_catalogo", return_value=SimpleNamespace()).start()
        mock.patch.object(reconciliacao, "TIPO_POR_TABELA", {"orcamento_api_orcamentos": "orcamentos"}).start()
        self.addCleanup(mock.patch.stopall)
        self.repassador = RepassadorFalso()

    def rodar(self, erp, paradas):
        self.fila.listar_aguardando_retorno.return_value = paradas
        return Reconciliacao(MotorFalso(), erp, self.repassador, configuracao()).executar_ciclo()

    def test_payload_string_e_decodificado(self):
        self.assertEqual(extrair_payload_requisicao(CONSULTA_PROCESSADA), RESULTADO)
        self.assertIsNone(extrair_payload_requisicao({"data": {"status_processamento": "Na fila"}}))

    def test_payload_com_quebra_de_linha_crua_do_erp(self):
        # O ERP real grava o payload com quebra de linha crua dentro das
        # strings (descrição do item): JSON inválido no modo estrito. Era o que
        # travava a reconciliação das requisições 35249-35252 (lote 31 de testing).
        payload = '{"success":true,"data":{"id_orcamento":19316,"itens":[{"id":1,"descricao":"com 10 páginas\n\nMiolo"}]}}'
        consulta = {"data": {"id_requisicao": 35249, "status_processamento": "Processado", "payload": payload}}
        resultado = extrair_payload_requisicao(consulta)
        self.assertEqual(resultado["data"]["id_orcamento"], 19316)
        self.assertEqual(resultado["data"]["itens"][0]["descricao"], "com 10 páginas\n\nMiolo")

    def test_requisicao_processada_no_erp_e_repassada(self):
        resolvidas = self.rodar(ErpConsulta(CONSULTA_PROCESSADA), [{"id": 700, "id_requisicao": 40430, "horas_esperando": 1}])
        self.assertEqual(resolvidas, 1)
        self.assertEqual(self.repassador.enviados, [("orcamentos", 700, RESULTADO)])

    def test_ainda_na_fila_do_erp_e_dentro_do_limite_espera(self):
        consulta = {"data": {"id_requisicao": 40430, "status_processamento": "Na fila"}}
        self.assertEqual(self.rodar(ErpConsulta(consulta), [{"id": 700, "id_requisicao": 40430, "horas_esperando": 2}]), 0)
        self.assertEqual(self.repassador.enviados, [])

    def test_passado_o_limite_libera_com_erro(self):
        consulta = {"data": {"id_requisicao": 40430, "status_processamento": "Na fila"}}
        self.rodar(ErpConsulta(consulta), [{"id": 700, "id_requisicao": 40430, "horas_esperando": 7}])
        _, _, corpo = self.repassador.enviados[0]
        self.assertFalse(corpo["success"])
        self.assertIn("40430", corpo["message"])

    def test_sem_id_requisicao_so_libera_depois_do_limite(self):
        self.assertEqual(self.rodar(ErpConsulta(), [{"id": 700, "id_requisicao": None, "horas_esperando": 1}]), 0)
        self.rodar(ErpConsulta(), [{"id": 701, "id_requisicao": None, "horas_esperando": 6.5}])
        self.assertIn("Confira no ERP", self.repassador.enviados[0][2]["message"])

    def test_erp_fora_nao_marca_erro(self):
        self.rodar(ErpConsulta(erro=ErpIndisponivel("503")), [{"id": 700, "id_requisicao": 40430, "horas_esperando": 8}])
        self.assertEqual(self.repassador.enviados, [])


class TestRepassador(unittest.TestCase):
    def test_guarda_o_que_o_pageflow_nao_recebeu_e_reenvia_depois(self):
        estado = {"no_ar": False}
        recebidos = []

        def roteador(request):
            if not estado["no_ar"]:
                return httpx.Response(503)
            recebidos.append((request.url.path, json.loads(request.content), request.headers["x-api-key"]))
            return httpx.Response(200, json={"sucesso": True, "dados": {"ja_processado": False}})

        cliente = PageflowClient(
            configuracao(PAGEFLOW_TENTATIVAS=2),
            http=httpx.Client(transport=httpx.MockTransport(roteador)),
            dormir=lambda s: None,
        )
        with tempfile.TemporaryDirectory() as pasta:
            repassador = Repassador(cliente, pasta)

            resultado = repassador.orcamento(700, RESULTADO)
            self.assertIs(resultado.desfecho, Desfecho.NAO_ENTREGUE)
            self.assertEqual(repassador.pendentes("orcamentos"), {700})

            estado["no_ar"] = True
            self.assertEqual(repassador.reenviar_pendentes(), 1)
            self.assertEqual(repassador.pendentes("orcamentos"), set())
            self.assertEqual(recebidos, [("/api/pcp/retorno/orcamentos/700", RESULTADO, "pk_test_x")])

    def test_409_descarta_sem_guardar(self):
        cliente = PageflowClient(
            configuracao(),
            http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(409, json={"codigo": "RETORNO_FORA_DE_ESTADO"}))),
            dormir=lambda s: None,
        )
        with tempfile.TemporaryDirectory() as pasta:
            repassador = Repassador(cliente, pasta)
            self.assertIs(repassador.aprovacao(900, {}).desfecho, Desfecho.DESCARTADO)
            self.assertEqual(os.listdir(os.path.join(pasta, "repasses_pendentes")), [])


if __name__ == "__main__":
    unittest.main()
