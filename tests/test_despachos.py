import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

from deskflow2.clientes.erp import ErpIndisponivel, FalhaLogin, ResultadoIncerto
from deskflow2.repositorios.fila import AprovacaoReivindicada, OrcamentoReivindicado
from deskflow2.servicos import despacho_aprovacao, despacho_orcamento
from deskflow2.servicos.comum import chamar_erp, corpo_para_auditoria
from deskflow2.servicos.despacho_aprovacao import DespachoAprovacoes, itens_ja_aprovados
from deskflow2.servicos.despacho_orcamento import DespachoOrcamentos
from deskflow2.servicos.payload import PayloadIncompleto
from tests.apoio import MotorFalso, RepassadorFalso, configuracao


def resposta(status, corpo):
    return httpx.Response(status, json=corpo)


class ErpFalso:
    def __init__(self, resposta_post=None, erro_post=None, resposta_get=None, erro_get=None, erro_login=None):
        self.resposta_post = resposta_post
        self.erro_post = erro_post
        self.resposta_get = resposta_get
        self.erro_get = erro_get
        self.erro_login = erro_login
        self.posts = []
        self.gets = []

    def garantir_login(self):
        if self.erro_login:
            raise self.erro_login

    def post(self, caminho, corpo):
        self.posts.append((caminho, corpo))
        if self.erro_post:
            raise self.erro_post
        return self.resposta_post

    def get(self, caminho, params=None):
        self.gets.append((caminho, params))
        if self.erro_get:
            raise self.erro_get
        return self.resposta_get


class TestChamarErp(unittest.TestCase):
    def test_ack_assincrono_devolve_id_requisicao(self):
        erp = ErpFalso(resposta_post=resposta(200, {"success": True, "assincrono": True, "data": {"id_requisicao": 40550}}))
        resultado = chamar_erp(erp, "/api/v1/orcamento", {}, "assincrono", "id_orcamento")
        self.assertEqual(resultado.id_requisicao, 40550)
        self.assertIsNone(resultado.corpo_retorno)

    def test_assincrono_que_ja_devolveu_o_resultado_e_repassado(self):
        corpo = {"success": True, "data": {"id_orcamento": 1, "id_requisicao": 5, "itens": []}}
        resultado = chamar_erp(ErpFalso(resposta_post=resposta(200, corpo)), "/x", {}, "assincrono", "id_orcamento")
        self.assertEqual(resultado.corpo_retorno, corpo)

    def test_erro_de_validacao_do_erp_e_repassado_como_veio(self):
        corpo = {"sucess": False, "code": 400, "data": {"error": "Dados incompletos"}}
        resultado = chamar_erp(ErpFalso(resposta_post=resposta(400, corpo)), "/x", {}, "assincrono", "id_orcamento")
        self.assertEqual(resultado.corpo_retorno, corpo)

    def test_resultado_incerto_vira_erro_com_aviso(self):
        resultado = chamar_erp(ErpFalso(erro_post=ResultadoIncerto("timeout")), "/x", {}, "sincrono", "id_orcamento")
        self.assertFalse(resultado.corpo_retorno["success"])
        self.assertIn("Confira no ERP", resultado.corpo_retorno["message"])

    def test_erp_indisponivel_vira_erro(self):
        resultado = chamar_erp(ErpFalso(erro_post=ErpIndisponivel("503")), "/x", {}, "sincrono", "id_orcamento")
        self.assertIn("indisponível", resultado.corpo_retorno["message"])

    def test_aprovacao_assincrona_so_e_ack_sem_a_lista_data(self):
        ack = {"success": True, "data": {"id_requisicao": 9}}
        final = {"success": True, "id_requisicao": 9, "data": [{"id_ops": "1", "pedidos": [], "ops": []}]}
        self.assertEqual(chamar_erp(ErpFalso(resposta_post=resposta(200, ack)), "/x", {}, "assincrono", "data").id_requisicao, 9)
        self.assertEqual(chamar_erp(ErpFalso(resposta_post=resposta(200, final)), "/x", {}, "assincrono", "data").corpo_retorno, final)

    def test_auditoria_nao_guarda_a_url_do_webhook(self):
        corpo = {"identifier": "PageFlow", "url_webhook": "https://pf/api/pcp/webhook/orcamentos/1/SEGREDO"}
        self.assertEqual(corpo_para_auditoria(corpo)["url_webhook"], "[omitida]")
        self.assertIn("SEGREDO", corpo["url_webhook"])


def orcamento_reivindicado(**campos):
    base = dict(
        id=700, requisicao_id=300, lote_id=5, modo_envio="assincrono",
        url_webhook="https://pf/api/pcp/webhook/orcamentos/700/tok",
        modo_agrupamento="unidade", cliente_id=501, vendedor_id=7, forma_pagamento=11,
        pedido_distribuicao_ids=[1],
    )
    base.update(campos)
    return OrcamentoReivindicado(**base)


class TestDespachoOrcamentos(unittest.TestCase):
    def setUp(self):
        self.fila = mock.patch.object(despacho_orcamento, "fila").start()
        self.fila.TABELA_ORCAMENTOS = "orcamento_api_orcamentos"
        mock.patch.object(despacho_orcamento, "carregar_catalogo", return_value=SimpleNamespace()).start()
        self.montar = mock.patch.object(despacho_orcamento, "montar_payload_orcamento").start()
        self.montar.return_value = {"identifier": "PageFlow", "data": {"itens": [{"codigo_externo": "1"}]}}
        self.addCleanup(mock.patch.stopall)
        self.repassador = RepassadorFalso()

    def despacho(self, erp, **config):
        return DespachoOrcamentos(MotorFalso(), erp, self.repassador, configuracao(**config), dormir=lambda s: None)

    def test_assincrono_manda_url_webhook_grava_id_requisicao_e_para(self):
        self.fila.reivindicar_orcamento.return_value = orcamento_reivindicado()
        erp = ErpFalso(resposta_post=resposta(200, {"success": True, "data": {"id_requisicao": 40550}}))

        self.assertEqual(self.despacho(erp).despachar(700), "aguardando_webhook")

        enviado = erp.posts[0][1]
        self.assertTrue(enviado["assincrono"])
        self.assertIn("/orcamentos/700/tok", enviado["url_webhook"])
        auditoria = self.fila.gravar_payload_enviado.call_args.args[3]
        self.assertEqual(auditoria["url_webhook"], "[omitida]")
        self.assertEqual(self.fila.gravar_id_requisicao.call_args.args[4], 40550)
        self.assertEqual(self.repassador.enviados, [])

    def test_sincrono_repassa_a_resposta_como_veio(self):
        self.fila.reivindicar_orcamento.return_value = orcamento_reivindicado(modo_envio="sincrono")
        corpo = {"success": True, "data": {"id_orcamento": 36191, "itens": [{"id": 1, "codigo_externo": "1"}]}}
        erp = ErpFalso(resposta_post=resposta(200, corpo))

        self.assertEqual(self.despacho(erp).despachar(700), "repassado")

        self.assertNotIn("assincrono", erp.posts[0][1])
        self.assertEqual(self.repassador.enviados, [("orcamentos", 700, corpo)])

    def test_payload_incompleto_nao_chama_o_erp_e_repassa_erro(self):
        self.fila.reivindicar_orcamento.return_value = orcamento_reivindicado()
        self.montar.side_effect = PayloadIncompleto("pedidos sem item no orçamento: [1]")
        erp = ErpFalso()

        self.assertEqual(self.despacho(erp).despachar(700), "payload_incompleto")

        self.assertEqual(erp.posts, [])
        tipo, registro_id, corpo = self.repassador.enviados[0]
        self.assertEqual((tipo, registro_id, corpo["success"]), ("orcamentos", 700, False))

    def test_claim_perdido_nao_chama_o_erp(self):
        self.fila.reivindicar_orcamento.return_value = None
        erp = ErpFalso()
        self.assertEqual(self.despacho(erp).despachar(700), "ja_reivindicado")
        self.assertEqual(erp.posts, [])

    def test_login_recusado_nao_reivindica_nada(self):
        self.fila.listar_orcamentos_pendentes.return_value = [700, 701]
        erp = ErpFalso(erro_login=FalhaLogin("Login inválido"))

        self.assertEqual(self.despacho(erp).executar_ciclo(), 0)
        self.fila.reivindicar_orcamento.assert_not_called()


def aprovacao_reivindicada(**campos):
    base = dict(
        id=900, orcamento_id=700, id_orcamento=36191, gerar_op=True,
        itens_aprovados=[{"id": 71928, "data_entrega": "2026-09-20T18:00:00.000-03:00"}],
        modo_envio="sincrono", url_webhook=None,
    )
    base.update(campos)
    return AprovacaoReivindicada(**base)


class TestDespachoAprovacoes(unittest.TestCase):
    def setUp(self):
        self.fila = mock.patch.object(despacho_aprovacao, "fila").start()
        self.fila.TABELA_APROVACOES = "orcamento_api_aprovacoes"
        self.fila.id_orcamento_da_aprovacao.return_value = 36191
        mock.patch.object(despacho_aprovacao, "carregar_catalogo", return_value=SimpleNamespace()).start()
        self.addCleanup(mock.patch.stopall)
        self.repassador = RepassadorFalso()

    def despacho(self, erp):
        return DespachoAprovacoes(MotorFalso(), erp, self.repassador, configuracao(), dormir=lambda s: None)

    def test_consulta_antes_e_aprova_com_data_de_entrega(self):
        self.fila.reivindicar_aprovacao.return_value = aprovacao_reivindicada()
        consulta = {"success": True, "data": [{"itens": [{"id": 71928, "status": "Pendente"}]}]}
        final = {"success": True, "data": [{"id_ops": "1", "pedidos": [{"serie": "PV", "id": 1, "empresa": 1}], "ops": []}]}
        erp = ErpFalso(resposta_get=resposta(200, consulta), resposta_post=resposta(200, final))

        self.assertEqual(self.despacho(erp).despachar(900), "repassado")

        self.assertEqual(erp.gets[0][1], {"id_orcamento": 36191, "apenas_ultima": "true"})
        caminho, corpo = erp.posts[0]
        self.assertEqual(caminho, "/api/v1/proposta/aprovar")
        self.assertEqual(corpo["data"]["itens"][0]["data_entrega"], "2026-09-20T18:00:00.000-03:00")
        self.assertTrue(corpo["data"]["gerar_op"])
        self.assertEqual([e[0] for e in self.repassador.enviados], ["consulta", "aprovacoes"])

    def test_proposta_ja_confirmada_nao_aprova_de_novo(self):
        self.fila.reivindicar_aprovacao.return_value = aprovacao_reivindicada()
        consulta = {"success": True, "data": [{"itens": [{"id": 71928, "status": "Confirmada"}]}]}
        erp = ErpFalso(resposta_get=resposta(200, consulta))

        self.assertEqual(self.despacho(erp).despachar(900), "ja_aprovada")

        self.assertEqual(erp.posts, [])
        self.assertEqual(self.repassador.enviados[-1], ("aprovacoes", 900, consulta))

    def test_consulta_indisponivel_deixa_a_aprovacao_pendente(self):
        erp = ErpFalso(erro_get=ErpIndisponivel("503"))

        self.assertEqual(self.despacho(erp).despachar(900), "consulta_indisponivel")

        self.fila.reivindicar_aprovacao.assert_not_called()
        self.assertEqual(self.repassador.enviados, [])

    def test_itens_ja_aprovados_exige_todos_confirmados(self):
        consulta = {"data": [{"itens": [{"id": 1, "status": "Confirmada"}, {"id": 2, "status": "Pendente"}]}]}
        self.assertTrue(itens_ja_aprovados(consulta, [{"id": 1}]))
        self.assertFalse(itens_ja_aprovados(consulta, [{"id": 1}, {"id": 2}]))
        self.assertFalse(itens_ja_aprovados(consulta, []))


if __name__ == "__main__":
    unittest.main()
