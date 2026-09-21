import json
import unittest

import httpx

from deskflow2.clientes.erp import ErpClient, ErpIndisponivel, FalhaLogin, ResultadoIncerto, sucesso_erp
from tests.apoio import configuracao


class Relogio:
    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora

    def dormir(self, segundos):
        self.agora += segundos


def cliente(roteador, **config):
    relogio = Relogio()
    http = httpx.Client(transport=httpx.MockTransport(roteador))
    return ErpClient(configuracao(**config), http=http, relogio=relogio, dormir=relogio.dormir), relogio


def login_ok(request):
    return httpx.Response(200, json={"sucess": True, "code": 200, "data": {"token": "tok-1"}})


class TestErpClient(unittest.TestCase):
    def test_login_e_envio_com_os_dois_headers_de_token(self):
        vistos = []

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return login_ok(request)
            vistos.append(request.headers)
            return httpx.Response(200, json={"success": True, "data": {"id_orcamento": 1}})

        erp, _ = cliente(roteador)
        resposta = erp.post("/api/v1/orcamento", {"a": 1})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(vistos[0]["token"], "tok-1")
        self.assertEqual(vistos[0]["authorization"], "Bearer tok-1")

    def test_503_repete_ate_dar_certo(self):
        chamadas = {"n": 0}

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return login_ok(request)
            chamadas["n"] += 1
            if chamadas["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"success": True})

        erp, relogio = cliente(roteador)
        erp.post("/api/v1/orcamento", {})

        self.assertEqual(chamadas["n"], 3)
        self.assertEqual(relogio.agora, 5 + 10)

    def test_503_ate_o_fim_da_janela_vira_erp_indisponivel(self):
        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return login_ok(request)
            return httpx.Response(503)

        erp, _ = cliente(roteador, ERP_503_MAX_WAIT_SECONDS=20)
        with self.assertRaises(ErpIndisponivel):
            erp.post("/api/v1/orcamento", {})

    def test_timeout_de_leitura_em_post_nao_repete(self):
        chamadas = {"n": 0}

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return login_ok(request)
            chamadas["n"] += 1
            raise httpx.ReadTimeout("lento", request=request)

        erp, _ = cliente(roteador)
        with self.assertRaises(ResultadoIncerto):
            erp.post("/api/v1/orcamento", {})
        self.assertEqual(chamadas["n"], 1)

    def test_timeout_de_leitura_em_get_repete(self):
        chamadas = {"n": 0}

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return login_ok(request)
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                raise httpx.ReadTimeout("lento", request=request)
            return httpx.Response(200, json={"success": True, "data": []})

        erp, _ = cliente(roteador)
        self.assertEqual(erp.get("/api/v1/proposta", {"id_orcamento": 1}).status_code, 200)
        self.assertEqual(chamadas["n"], 2)

    def test_2xx_ilegivel_em_post_e_resultado_incerto(self):
        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return login_ok(request)
            return httpx.Response(200, text="<html>proxy</html>")

        erp, _ = cliente(roteador)
        with self.assertRaises(ResultadoIncerto):
            erp.post("/api/v1/orcamento", {})

    def test_401_renova_o_token_uma_vez(self):
        logins = {"n": 0}

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                logins["n"] += 1
                return httpx.Response(200, json={"success": True, "data": {"token": f"tok-{logins['n']}"}})
            if request.headers["token"] == "tok-1":
                return httpx.Response(401, json={"message": "Token inválido"})
            return httpx.Response(200, json={"success": True})

        erp, _ = cliente(roteador)
        self.assertEqual(erp.post("/api/v1/orcamento", {}).status_code, 200)
        self.assertEqual(logins["n"], 2)

    def test_login_recusado(self):
        def roteador(request):
            return httpx.Response(401, json={"sucess": False, "message": "Login inválido"})

        erp, _ = cliente(roteador)
        with self.assertRaises(FalhaLogin) as ctx:
            erp.garantir_login()
        self.assertIn("Login inválido", str(ctx.exception))

    def test_token_renovado_antes_de_vencer(self):
        logins = {"n": 0}

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                logins["n"] += 1
                return login_ok(request)
            return httpx.Response(200, json={"success": True})

        erp, relogio = cliente(roteador)
        erp.garantir_login()
        relogio.agora += 7200 - 300
        erp.garantir_login()
        self.assertEqual(logins["n"], 2)

    def test_sucesso_erp_aceita_as_duas_grafias(self):
        self.assertTrue(sucesso_erp({"success": True, "code": 200}))
        self.assertTrue(sucesso_erp({"sucess": True}))
        self.assertFalse(sucesso_erp({"success": True, "code": 400}))
        self.assertFalse(sucesso_erp(json.loads('{"success": "true"}')))


if __name__ == "__main__":
    unittest.main()
