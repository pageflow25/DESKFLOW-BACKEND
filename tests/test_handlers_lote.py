"""Handlers da Fase 3: planilha por linha, sincronização paginada e importação
de produto.

Sem banco e sem rede: o ERP é um `httpx.MockTransport`, como em
`test_handlers_cliente.py`, de onde vêm os dublês compartilhados (um ERP falso e
um `ItemReivindicado` de mentira) para não haver duas cópias deles.
Nenhuma chamada real ao ERP em teste nenhum.
"""

import json
import unittest

import httpx

from deskflow2.fila.modelos import Desfecho, Estado, Preparo
from deskflow2.fila.registry import registry_padrao
from deskflow2.handlers import (
    HandlerClientePlanilhaLinha,
    HandlerClienteSincronizarPagina,
    HandlerProdutoImportar,
)
from deskflow2.handlers.comum import PayloadInvalido
from tests.test_handlers_cliente import CLIENTE_ERP, DOCUMENTO, _erp, _executar, _item, _roteador

PRODUTO_ERP = {
    "id_produto": 815,
    "descricao": "Caderno 96 folhas",
    "componentes": [{"id_componente": 1, "descricao": "Miolo"}],
}


class TestPlanilhaLinha(unittest.TestCase):
    """A linha da planilha é a MESMA chamada de cliente.criar/atualizar; o que
    estes testes guardam é que ela continua sendo a mesma."""

    def _payload(self, operacao="criar", **extras):
        payload = {
            "operacao": operacao,
            "linha": 7,
            "id_cliente": 4242 if operacao == "atualizar" else None,
            "documento": DOCUMENTO,
            "cliente": {"cnpj": DOCUMENTO, "nome": "Escola Teste", "razao_social": "Escola Teste LTDA"},
            "extras": {"observacao": "importado da planilha"},
        }
        payload.update(extras)
        return payload

    def _handler(self, roteador):
        return HandlerClientePlanilhaLinha(_erp(roteador))

    def test_criar_faz_post_e_devolve_id_cliente(self):
        roteador, vistos = _roteador(POST=httpx.Response(
            200, json={"success": True, "code": 200, "data": {"id_cliente": 4242}}))
        handler = self._handler(roteador)

        desfecho, preparo = _executar(handler, _item(self._payload(), tipo="cliente.planilha_linha"))

        corpo = json.loads(vistos[0].content)
        self.assertEqual(vistos[0].method, "POST")
        self.assertEqual(corpo["identifier"], "PageFlow")
        self.assertEqual(corpo["data"]["cnpj"], DOCUMENTO)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        # A origem viaja no resultado e na auditoria: é o que liga o item à
        # linha do arquivo numa planilha meio aplicada.
        self.assertEqual(desfecho.resultado["linha"], 7)
        self.assertEqual(desfecho.resultado["operacao"], "criar")
        self.assertEqual(preparo.payload_enviado["linha"], 7)
        self.assertEqual(preparo.payload_enviado["caminho"], "/api/v1/cliente")

    def test_atualizar_faz_patch_com_o_id_no_corpo(self):
        roteador, vistos = _roteador(PATCH=httpx.Response(
            200, json={"success": True, "code": 200, "data": {"id_cliente": 4242}}))
        handler = self._handler(roteador)

        desfecho, preparo = _executar(
            handler, _item(self._payload("atualizar"), tipo="cliente.planilha_linha"))

        corpo = json.loads(vistos[0].content)
        self.assertEqual(vistos[0].method, "PATCH")
        self.assertEqual(corpo["data"]["id_cliente"], 4242)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        self.assertEqual(preparo.payload_enviado["operacao"], "atualizar")

    def test_operacao_ausente_nao_chega_ao_erp_e_e_falha_definitiva(self):
        # Deduzir a operação a partir de `id_cliente` transformaria uma edição
        # que perdeu o id num cadastro duplicado no ERP.
        roteador, vistos = _roteador()
        handler = self._handler(roteador)
        item = _item(self._payload(operacao=None), tipo="cliente.planilha_linha")

        preparo = handler.preparar(None, item)
        bruto = preparo.chamar()

        self.assertIsInstance(bruto, PayloadInvalido)
        self.assertEqual(vistos, [])
        desfecho = handler.interpretar(item, bruto)
        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "PLANILHA_OPERACAO_INVALIDA")

    def test_operacao_desconhecida_tambem_e_falha_definitiva(self):
        roteador, vistos = _roteador()
        handler = self._handler(roteador)
        item = _item(self._payload(operacao="remover"), tipo="cliente.planilha_linha")

        desfecho = handler.interpretar(item, handler.preparar(None, item).chamar())

        self.assertEqual(vistos, [])
        self.assertIs(desfecho.estado, Estado.FALHOU)

    def test_erro_de_negocio_e_falha_definitiva_sem_gastar_tentativa(self):
        roteador, _ = _roteador(POST=httpx.Response(
            400, json={"success": False, "message": "CNPJ inválido"}))
        handler = self._handler(roteador)

        desfecho, _ = _executar(handler, _item(self._payload(), tipo="cliente.planilha_linha"))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_HTTP_400")
        self.assertIn("CNPJ inválido", desfecho.erro)

    def test_resultado_incerto_no_criar_e_resolvido_pela_verificacao(self):
        # 500 numa escrita é incerto; o `verificar` pergunta ao ERP pelo
        # documento e fecha o item com o id verdadeiro.
        roteador, vistos = _roteador(
            POST=httpx.Response(500, json={"message": "erro interno"}),
            GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}),
        )
        handler = self._handler(roteador)
        item = _item(self._payload(), tipo="cliente.planilha_linha")

        incerto, _ = _executar(handler, item)
        self.assertIs(incerto.estado, Estado.INCERTO)

        desfecho = handler.verificar(None, item)

        self.assertEqual(vistos[1].method, "GET", "a verificação precisa ser leitura")
        self.assertEqual(vistos[1].url.params["cpfcnpj"], DOCUMENTO)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        self.assertEqual(desfecho.resultado["verificado_por"], "cliente.consultar")
        self.assertEqual(desfecho.resultado["linha"], 7)

    def test_verificacao_do_atualizar_devolve_a_linha_a_fila_quando_diverge(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}))
        handler = self._handler(roteador)
        payload = self._payload("atualizar")
        payload["cliente"]["nome"] = "Nome novo"

        desfecho = handler.verificar(None, _item(payload, tipo="cliente.planilha_linha"))

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "VERIFICACAO_NAO_APLICADA")

    def test_consulta_que_falha_mantem_a_linha_incerta(self):
        roteador, _ = _roteador(GET=httpx.Response(500, json={"message": "indisponível"}))
        handler = self._handler(roteador)

        self.assertIsNone(handler.verificar(None, _item(self._payload(), tipo="cliente.planilha_linha")))

    def test_delega_aos_handlers_de_cliente_em_vez_de_reimplementar(self):
        """O valor do desenho é não haver uma segunda implementação da chamada:
        este teste falha se alguém copiar a lógica para dentro do handler."""
        chamados = []

        class Espiao:
            def preparar(self, conn, item):
                chamados.append("preparar")
                return Preparo(payload_enviado={"metodo": "POST"}, chamar=lambda: None)

            def interpretar(self, item, bruto):
                chamados.append("interpretar")
                return Desfecho.concluido({"id_cliente": 1})

            def verificar(self, conn, item):
                chamados.append("verificar")
                return None

        roteador, vistos = _roteador()
        handler = HandlerClientePlanilhaLinha(_erp(roteador), criar=Espiao())
        item = _item(self._payload(), tipo="cliente.planilha_linha")

        preparo = handler.preparar(None, item)
        handler.interpretar(item, preparo.chamar())
        handler.verificar(None, item)

        self.assertEqual(chamados, ["preparar", "interpretar", "verificar"])
        self.assertEqual(vistos, [])


class TestSincronizarPagina(unittest.TestCase):
    def _resposta(self, *, pages=None, clientes=(CLIENTE_ERP,)):
        corpo = {"success": True, "code": 200, "data": list(clientes)}
        if pages is not None:
            corpo["metadata"] = {"pages": pages}
        return httpx.Response(200, json=corpo)

    def test_primeira_pagina_devolve_clientes_e_total_de_paginas(self):
        roteador, vistos = _roteador(GET=self._resposta(pages=45))
        handler = HandlerClienteSincronizarPagina(_erp(roteador))

        desfecho, preparo = _executar(
            handler, _item({"pagina": 1}, tipo="cliente.sincronizar_pagina", classe="assincrono"))

        self.assertEqual(vistos[0].method, "GET")
        self.assertEqual(vistos[0].url.params["page"], "1")
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["pagina"], 1)
        self.assertEqual(desfecho.resultado["total_paginas"], 45)
        self.assertEqual(desfecho.resultado["clientes"], [CLIENTE_ERP])
        self.assertEqual(preparo.payload_enviado["params"], {"page": 1})

    def test_pagina_1_sem_total_de_paginas_falha_de_forma_visivel(self):
        # Sem `metadata.pages` o projetor não enfileira as páginas 2..N: a
        # sincronização terminaria com 1 página espelhada e 44 nunca pedidas.
        roteador, _ = _roteador(GET=self._resposta(pages=None))
        handler = HandlerClienteSincronizarPagina(_erp(roteador))

        desfecho, _ = _executar(
            handler, _item({"pagina": 1}, tipo="cliente.sincronizar_pagina", classe="assincrono"))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_SEM_TOTAL_PAGINAS")
        self.assertIn("metadata.pages", desfecho.erro)

    def test_pagina_1_com_total_invalido_tambem_falha(self):
        roteador, _ = _roteador(GET=httpx.Response(
            200, json={"success": True, "data": [], "metadata": {"pages": 0}}))
        handler = HandlerClienteSincronizarPagina(_erp(roteador))

        desfecho, _ = _executar(
            handler, _item({"pagina": 1}, tipo="cliente.sincronizar_pagina", classe="assincrono"))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_SEM_TOTAL_PAGINAS")

    def test_pagina_seguinte_sem_total_de_paginas_segue_valida(self):
        # Só a página 1 encadeia; nas demais o total é ignorado pelo projetor.
        roteador, _ = _roteador(GET=self._resposta(pages=None))
        handler = HandlerClienteSincronizarPagina(_erp(roteador))

        desfecho, _ = _executar(
            handler, _item({"pagina": 12}, tipo="cliente.sincronizar_pagina", classe="assincrono"))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["pagina"], 12)
        self.assertIsNone(desfecho.resultado["total_paginas"])

    def test_pagina_invalida_nao_chega_ao_erp(self):
        roteador, vistos = _roteador()
        handler = HandlerClienteSincronizarPagina(_erp(roteador))
        item = _item({"pagina": 0}, tipo="cliente.sincronizar_pagina")

        desfecho = handler.interpretar(item, handler.preparar(None, item).chamar())

        self.assertEqual(vistos, [])
        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "PAYLOAD_INVALIDO")

    def test_502_do_erp_e_retentavel(self):
        roteador, _ = _roteador(GET=httpx.Response(502, text="bad gateway"))
        handler = HandlerClienteSincronizarPagina(_erp(roteador))

        desfecho, _ = _executar(
            handler, _item({"pagina": 3}, tipo="cliente.sincronizar_pagina", classe="assincrono"))

        self.assertIs(desfecho.estado, Estado.RETENTAR)

    def test_leitura_nunca_pede_verificacao(self):
        roteador, vistos = _roteador()
        handler = HandlerClienteSincronizarPagina(_erp(roteador))

        self.assertIsNone(handler.verificar(None, _item({"pagina": 1}, tipo="cliente.sincronizar_pagina")))
        self.assertEqual(vistos, [])


class TestProdutoImportar(unittest.TestCase):
    def _payload(self, **extras):
        payload = {"id_produto": 815, "id_categoria": 3, "origem_erp": 2}
        payload.update(extras)
        return payload

    def test_caminho_feliz_devolve_o_produto_cru(self):
        roteador, vistos = _roteador(GET=httpx.Response(
            200, json={"success": True, "code": 200, "data": [PRODUTO_ERP]}))
        handler = HandlerProdutoImportar(_erp(roteador))

        desfecho, preparo = _executar(
            handler, _item(self._payload(), tipo="produto.importar", classe="assincrono"))

        self.assertEqual(vistos[0].method, "GET")
        self.assertEqual(vistos[0].url.path, "/api/v1/caracteristicasproduto")
        self.assertEqual(vistos[0].url.params["id"], "815")
        self.assertEqual(vistos[0].url.params["origem"], "2")
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["produto"], PRODUTO_ERP)
        self.assertEqual(desfecho.resultado["id_categoria"], 3)
        self.assertEqual(preparo.payload_enviado["params"], {"id": 815, "origem": 2})

    def test_origem_ausente_usa_o_padrao_2(self):
        roteador, vistos = _roteador(GET=httpx.Response(
            200, json={"success": True, "data": PRODUTO_ERP}))
        handler = HandlerProdutoImportar(_erp(roteador))

        desfecho, _ = _executar(
            handler, _item(self._payload(origem_erp=None), tipo="produto.importar"))

        self.assertEqual(vistos[0].url.params["origem"], "2")
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["produto"], PRODUTO_ERP)

    def test_sem_id_categoria_nao_chega_ao_erp(self):
        # O id_categoria não vai ao ERP, mas sem ele o projetor lançaria com a
        # chamada já gasta e o item já concluído.
        roteador, vistos = _roteador()
        handler = HandlerProdutoImportar(_erp(roteador))
        item = _item(self._payload(id_categoria=None), tipo="produto.importar")

        desfecho = handler.interpretar(item, handler.preparar(None, item).chamar())

        self.assertEqual(vistos, [])
        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "PAYLOAD_INVALIDO")

    def test_sem_id_produto_nao_chega_ao_erp(self):
        roteador, vistos = _roteador()
        handler = HandlerProdutoImportar(_erp(roteador))
        item = _item(self._payload(id_produto="abc"), tipo="produto.importar")

        desfecho = handler.interpretar(item, handler.preparar(None, item).chamar())

        self.assertEqual(vistos, [])
        self.assertIs(desfecho.estado, Estado.FALHOU)

    def test_produto_inexistente_e_falha_definitiva(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": []}))
        handler = HandlerProdutoImportar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload(), tipo="produto.importar"))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_PRODUTO_SEM_CARACTERISTICAS")

    def test_erro_de_negocio_e_falha_definitiva(self):
        roteador, _ = _roteador(GET=httpx.Response(
            404, json={"success": False, "message": "Produto não encontrado"}))
        handler = HandlerProdutoImportar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload(), tipo="produto.importar"))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_HTTP_404")
        self.assertIn("Produto não encontrado", desfecho.erro)

    def test_500_em_leitura_e_retentavel_nao_incerto(self):
        roteador, _ = _roteador(GET=httpx.Response(500, json={"message": "erro interno"}))
        handler = HandlerProdutoImportar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload(), tipo="produto.importar"))

        self.assertIs(desfecho.estado, Estado.RETENTAR)

    def test_leitura_nunca_pede_verificacao(self):
        roteador, _ = _roteador()
        handler = HandlerProdutoImportar(_erp(roteador))

        self.assertIsNone(handler.verificar(None, _item(self._payload(), tipo="produto.importar")))


class TestRegistryDaFase3(unittest.TestCase):
    def test_registry_padrao_resolve_os_tipos_novos(self):
        roteador, _ = _roteador()
        registry = registry_padrao(_erp(roteador))

        self.assertIsInstance(registry.obter("cliente.planilha_linha"), HandlerClientePlanilhaLinha)
        self.assertIsInstance(registry.obter("cliente.sincronizar_pagina"), HandlerClienteSincronizarPagina)
        self.assertIsInstance(registry.obter("produto.importar"), HandlerProdutoImportar)

    def test_vendedor_listar_pagina_continua_sem_handler(self):
        # O endpoint de vendedores do ERP não está documentado e precisa ser
        # confirmado com a Bremen. Tipo ativo sem handler não perde item: o
        # motor devolve a linha a `pendente` com atraso e registra o erro.
        roteador, _ = _roteador()
        registry = registry_padrao(_erp(roteador))

        self.assertIsNone(registry.obter("vendedor.listar_pagina"))


if __name__ == "__main__":
    unittest.main()
