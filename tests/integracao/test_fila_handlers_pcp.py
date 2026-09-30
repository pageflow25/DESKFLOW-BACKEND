"""Ciclo completo de um item de PCP, do claim ao estado final.

O que só o banco responde:

- um envio ACEITO em modo assíncrono para em `aguardando_callback`, com o
  `id_requisicao` no `resultado` e SEM lease — a linha sai do worker e fica
  esperando o webhook, que é quem a conclui. Se isso virasse `concluido`, o
  PageFlow veria como pronto um orçamento que o ERP ainda nem montou;
- um resultado INCERTO de tipo não idempotente para em `incerto` e NÃO volta a
  `pendente`: nada de reenviar um orçamento que pode já existir no ERP;
- falha definitiva encerra sem consumir o orçamento de tentativas.

O ERP é um `httpx.MockTransport` — nenhuma chamada real. Os tipos são sorteados
por teste e apagados no fim; nenhum dos 10 tipos reais é tocado nem ativado.
Nenhuma tabela de domínio é escrita: o único SQL de domínio que roda aqui é o
SELECT dos três orçamentos, com ids que não existem.
"""

import unittest
from types import SimpleNamespace

import httpx

from deskflow2.integracoes.erp import ErpClient
from deskflow2.fila.catalogo import carregar_catalogo
from deskflow2.fila.motor import Motor
from deskflow2.fila.registry import Registry
from deskflow2.handlers import HandlerPcpAprovacaoEnviar, HandlerPcpOrcamentoEnviar
from tests.apoio import configuracao
from tests.integracao.apoio_banco import TesteDeFila

WEBHOOK = "https://pageflow.teste/api/pcp/webhook/aprovacao/900?token=segredo"

PAYLOAD_APROVACAO = {
    "aprovacao_id": 900,
    "orcamento_id": 700,
    "id_orcamento": 555,
    "gerar_op": True,
    "itens_aprovados": [{"id": 1}],
    "url_webhook": WEBHOOK,
}

# Orçamento com a origem que o produtor não deveria mandar. Este payload para
# ANTES de tocar o banco, e é de propósito: os três SQLs de orçamento não podem
# ser exercitados aqui sem criar pedido, arquivo e catálogo Bremen de verdade —
# tabela de domínio, que estes testes não escrevem. Quem cobre a escolha do SQL
# é `tests/test_handlers_pcp.py`, com a conexão dublada.
PAYLOAD_ORCAMENTO_ORIGEM_INVALIDA = {
    "orcamento_id": 700,
    "lote_id": 12,
    "requisicao_id": 40,
    "origem": "parceiro",
    "modo_agrupamento": "unidade",
    "cliente_id": 10,
    "vendedor_id": 20,
    "forma_pagamento": 3,
    "ids_origem": [101],
    "data_entrega": None,
    "url_webhook": None,
}

PROPOSTA_ABERTA = {"success": True, "data": [{"itens": [{"id": 1, "status": "Aberta"}]}]}


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


class TestHandlersDePcpNoCicloCompleto(TesteDeFila):
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

    def _processar(self, tipo, handler, payload, **item_extras):
        registry = Registry()
        registry.registrar(tipo, handler)
        with self.engine.connect() as conn:
            catalogo = carregar_catalogo(conn)
        item_id = self.criar_item(tipo, classe="assincrono", payload=payload, **item_extras)
        item = next(item for item in self.reivindicar() if item.id == item_id)
        motor = Motor(self.engine, catalogo, registry, _configuracao(), "worker-teste")
        return item_id, motor.processar(item)

    def test_aprovacao_aceita_em_modo_assincrono_espera_o_webhook(self):
        erp = self._erp({
            "GET": httpx.Response(200, json=PROPOSTA_ABERTA),
            "POST": httpx.Response(200, json={"success": True, "data": {"id_requisicao": 4321}}),
        })
        tipo = self.criar_tipo(idempotente=False, max_tentativas=5)

        item_id, estado = self._processar(tipo, HandlerPcpAprovacaoEnviar(erp), PAYLOAD_APROVACAO,
                                          max_tentativas=5)

        self.assertEqual(estado, "aguardando_callback")
        self.assertEqual(self.status_de(item_id), "aguardando_callback")
        linha = self.linha(item_id)
        self.assertEqual(linha["resultado"]["id_requisicao"], 4321)
        self.assertEqual(linha["resultado"]["modo_envio"], "assincrono")
        self.assertIsNone(linha["finalizado_em"], "quem finaliza é o webhook, no PageFlow")
        self.assertIsNone(linha["lease_expira_em"], "a linha não ocupa mais worker")
        # O corpo real fica auditável, e sem o token do webhook.
        self.assertEqual(linha["payload_enviado"]["url_webhook"], "[omitida]")
        self.assertEqual(self.eventos_de(item_id), ["executando", "aguardando_callback"])

    def test_resultado_incerto_de_tipo_nao_idempotente_nao_volta_a_pendente(self):
        # 2xx ilegível: a aprovação pode ter gerado OP e PV. Reenviar às cegas
        # duplicaria a produção, então o item para para decisão humana.
        erp = self._erp({
            "GET": httpx.Response(200, json=PROPOSTA_ABERTA),
            "POST": httpx.Response(200, text="<html>erro</html>"),
        })
        tipo = self.criar_tipo(idempotente=False, max_tentativas=5)

        item_id, estado = self._processar(tipo, HandlerPcpAprovacaoEnviar(erp), PAYLOAD_APROVACAO,
                                          max_tentativas=5)

        self.assertEqual(estado, "incerto")
        self.assertEqual(self.status_de(item_id), "incerto")
        linha = self.linha(item_id)
        self.assertEqual(linha["erro_codigo"], "RESULTADO_INCERTO")
        self.assertEqual(linha["tentativa"], 1)
        self.assertNotIn("retentativa_agendada", self.eventos_de(item_id))

    def test_aprovacao_sem_id_orcamento_falha_sem_gastar_tentativa(self):
        erp = self._erp({})
        tipo = self.criar_tipo(idempotente=False, max_tentativas=5)
        payload = {**PAYLOAD_APROVACAO, "id_orcamento": None}

        item_id, estado = self._processar(tipo, HandlerPcpAprovacaoEnviar(erp), payload,
                                          max_tentativas=5)

        self.assertEqual(estado, "falhou")
        linha = self.linha(item_id)
        self.assertEqual(linha["erro_codigo"], "SEM_ID_ORCAMENTO")
        self.assertEqual(linha["tentativa"], 1, "payload inválido não consome o orçamento de tentativas")
        self.assertIsNotNone(linha["finalizado_em"])

    def test_orcamento_com_payload_invalido_falha_sem_chamar_o_erp(self):
        erp = self._erp({})
        tipo = self.criar_tipo(idempotente=False, max_tentativas=5)

        item_id, estado = self._processar(tipo, HandlerPcpOrcamentoEnviar(erp),
                                          PAYLOAD_ORCAMENTO_ORIGEM_INVALIDA, max_tentativas=5)

        self.assertEqual(estado, "falhou")
        linha = self.linha(item_id)
        self.assertEqual(linha["erro_codigo"], "PAYLOAD_INVALIDO")
        self.assertEqual(linha["tentativa"], 1)
        self.assertIn("Origem do orçamento desconhecida", linha["erro"])
        # O motivo fica visível na tela da fila, ao lado do item.
        self.assertIn("Origem do orçamento desconhecida", linha["payload_enviado"]["erro"])


if __name__ == "__main__":
    unittest.main()
