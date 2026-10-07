"""Ciclo completo de um item de cliente, do claim ao estado final.

O que só o banco responde: se um resultado incerto resolvido pela verificação
realmente FECHA a linha, se o que não foi criado realmente VOLTA a pendente com
`disponivel_em` no futuro, e se a falha definitiva encerra sem gastar o
orçamento de tentativas.

O ERP é um `httpx.MockTransport` — nenhuma chamada real, em nenhum teste. Os
tipos são sorteados por teste (`teste.fila.<hex>`) e apagados no fim: nenhum dos
10 tipos reais é tocado e nenhum é ativado.
"""

import unittest
from types import SimpleNamespace

import httpx

from app.integracoes.erp import ErpClient
from app.fila.catalogo import carregar_catalogo
from app.fila.motor import Motor
from app.fila.registry import Registry
from app.controllers import HandlerClienteConsultar, HandlerClienteCriar
from tests.apoio import configuracao
from tests.integracao.apoio_banco import TesteDeFila

DOCUMENTO = "12345678000199"
CLIENTE_ERP = {"id_cliente": 4242, "nome": "Escola Teste", "cnpj": "12.345.678/0001-99"}
PAYLOAD = {"cliente": {"cnpj": DOCUMENTO, "nome": "Escola Teste"}, "documento": DOCUMENTO}


def _configuracao(**extras):
    padrao = dict(
        FILA_LEASE_MARGEM_SEGUNDOS=30,
        FILA_BACKOFF_SINCRONO_BASE=2.0,
        FILA_BACKOFF_SINCRONO_TETO=8.0,
        FILA_BACKOFF_ASSINCRONO_BASE=15.0,
        FILA_BACKOFF_ASSINCRONO_TETO=900.0,
    )
    padrao.update(extras)
    return configuracao(**padrao)


class TestHandlersDeClienteNoCicloCompleto(TesteDeFila):
    def _erp(self, por_metodo):
        relogio = SimpleNamespace(agora=0.0)

        def roteador(request):
            if request.url.path == "/api/v1/auth":
                return httpx.Response(200, json={"sucess": True, "data": {"token": "tok-1"}})
            resposta = por_metodo.get(request.method)
            if resposta is None:
                raise AssertionError(f"chamada inesperada: {request.method} {request.url}")
            return resposta(request) if callable(resposta) else resposta

        return ErpClient(
            _configuracao(), http=httpx.Client(transport=httpx.MockTransport(roteador)),
            relogio=lambda: relogio.agora,
            dormir=lambda segundos: setattr(relogio, "agora", relogio.agora + segundos),
        )

    def _motor(self, tipo_codigo, handler, erp):
        """O catálogo é recarregado porque o tipo do teste nasceu depois do
        `setUpClass` — e é dele que saem `idempotente` e `tipo_verificacao_codigo`."""
        registry = Registry()
        registry.registrar(tipo_codigo, handler)
        with self.engine.connect() as conn:
            catalogo = carregar_catalogo(conn)
        return Motor(self.engine, catalogo, registry, _configuracao(), "worker-teste")

    def _processar(self, tipo, handler, erp, **item_extras):
        item_id = self.criar_item(tipo, classe="assincrono", payload=PAYLOAD, **item_extras)
        item = next(item for item in self.reivindicar() if item.id == item_id)
        estado = self._motor(tipo, handler, erp).processar(item)
        return item_id, estado

    def test_criar_caminho_feliz_fecha_o_item_com_o_id_do_erp(self):
        erp = self._erp({"POST": httpx.Response(200, json={"success": True, "data": {"id_cliente": 4242}})})
        tipo = self.criar_tipo(idempotente=False, verificacao_propria=True)

        item_id, estado = self._processar(tipo, HandlerClienteCriar(erp), erp)

        self.assertEqual(estado, "concluido")
        self.assertEqual(self.status_de(item_id), "concluido")
        linha = self.linha(item_id)
        self.assertEqual(linha["resultado"]["id_cliente"], 4242)
        self.assertEqual(linha["payload_enviado"]["caminho"], "/api/v1/cliente")
        self.assertEqual(self.eventos_de(item_id), ["executando", "concluido"])

    def test_consultar_caminho_feliz_grava_os_clientes_no_resultado(self):
        erp = self._erp({"GET": httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]})})
        tipo = self.criar_tipo(idempotente=True)

        item_id, estado = self._processar(tipo, HandlerClienteConsultar(erp), erp)

        self.assertEqual(estado, "concluido")
        self.assertEqual(self.linha(item_id)["resultado"]["clientes"], [CLIENTE_ERP])

    def test_incerto_com_o_cliente_ja_no_erp_fecha_como_concluido(self):
        # POST sem resposta (ResultadoIncerto) + a consulta encontra o cliente:
        # a chamada aconteceu, o item fecha sozinho. É este passo que substitui
        # o "confira no ERP antes de reenviar".
        def estourar(request):
            raise httpx.ReadTimeout("lento", request=request)

        erp = self._erp({
            "POST": estourar,
            "GET": httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}),
        })
        tipo = self.criar_tipo(idempotente=False, verificacao_propria=True, max_tentativas=3)

        item_id, estado = self._processar(tipo, HandlerClienteCriar(erp), erp, max_tentativas=3)

        self.assertEqual(estado, "concluido")
        self.assertEqual(self.status_de(item_id), "concluido")
        self.assertEqual(self.linha(item_id)["resultado"]["id_cliente"], 4242)
        self.assertEqual(self.linha(item_id)["resultado"]["verificado_por"], "cliente.consultar")
        self.assertIn("verificacao", self.eventos_de(item_id))

    def test_incerto_sem_o_cliente_no_erp_volta_a_pendente(self):
        def estourar(request):
            raise httpx.ReadTimeout("lento", request=request)

        erp = self._erp({
            "POST": estourar,
            "GET": httpx.Response(200, json={"success": True, "data": []}),
        })
        tipo = self.criar_tipo(idempotente=False, verificacao_propria=True, max_tentativas=3)

        item_id, estado = self._processar(tipo, HandlerClienteCriar(erp), erp, max_tentativas=3)

        self.assertEqual(estado, "pendente")
        self.assertEqual(self.status_de(item_id), "pendente")
        linha = self.linha(item_id)
        self.assertEqual(linha["erro_codigo"], "VERIFICACAO_NAO_CRIADO")
        self.assertIsNone(linha["claim_por"])
        self.assertIsNone(linha["lease_expira_em"])
        self.assertIsNotNone(linha["disponivel_em"])
        # O atraso sai com jitter COMPLETO — `uniform(0, teto)` pode sortear
        # perto de zero —, então o que se afirma aqui é que a volta passou pelo
        # agendamento de retentativa, não um número mínimo de segundos.
        self.assertEqual(self.eventos_de(item_id),
                         ["executando", "verificacao", "retentativa_agendada"])

    def test_incerto_com_a_consulta_indisponivel_fica_incerto(self):
        def estourar(request):
            raise httpx.ReadTimeout("lento", request=request)

        erp = self._erp({"POST": estourar, "GET": httpx.Response(500, json={"message": "fora do ar"})})
        tipo = self.criar_tipo(idempotente=False, verificacao_propria=True, max_tentativas=3)

        item_id, estado = self._processar(tipo, HandlerClienteCriar(erp), erp, max_tentativas=3)

        self.assertEqual(estado, "incerto")
        self.assertEqual(self.status_de(item_id), "incerto")
        self.assertNotIn("verificacao", self.eventos_de(item_id))

    def test_erro_de_negocio_encerra_sem_gastar_o_orcamento_de_tentativas(self):
        erp = self._erp({"POST": httpx.Response(400, json={"success": False, "message": "CNPJ inválido"})})
        tipo = self.criar_tipo(idempotente=False, verificacao_propria=True, max_tentativas=5)

        item_id, estado = self._processar(tipo, HandlerClienteCriar(erp), erp, max_tentativas=5)

        self.assertEqual(estado, "falhou")
        linha = self.linha(item_id)
        self.assertEqual(linha["tentativa"], 1, "a falha definitiva não pode consumir as 5 tentativas")
        self.assertEqual(linha["erro_codigo"], "ERP_HTTP_400")
        self.assertIn("CNPJ inválido", linha["erro"])
        self.assertIsNotNone(linha["finalizado_em"])


if __name__ == "__main__":
    unittest.main()
