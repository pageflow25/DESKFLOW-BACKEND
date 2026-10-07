"""Handlers de PCP (Fase 5): orçamento, aprovação e download dos arquivos.

Sem banco e sem rede. O ERP é um `httpx.MockTransport` (como em `test_erp.py`) e
a conexão é um dublê que devolve as linhas que o SQL devolveria — o que se
exercita aqui é a decisão do handler, não o SQL, que é o mesmo do caminho antigo
e já tem teste em `test_payload.py`.

Duas invariantes desta fase têm teste próprio, porque são o que a migração podia
quebrar sem ninguém notar:

- item aceito em modo assíncrono termina em `aguardando_callback`, NUNCA em
  `concluido` (quem conclui é o webhook, no PageFlow);
- `pcp.orcamento.enviar` e `pcp.aprovacao.enviar` NÃO são idempotentes: um
  resultado incerto não pode virar retentativa automática.
"""

import os
import tempfile
import unittest
import uuid
from types import SimpleNamespace

import httpx

from app.integracoes.erp import ErpClient
from app.fila.catalogo import CODIGOS_OBRIGATORIOS, Catalogo, Tipo
from app.fila.modelos import Desfecho, Estado, ItemReivindicado
from app.fila.motor import Motor
from app.fila.registry import registry_padrao
from app.controllers import (
    HandlerPcpAprovacaoEnviar,
    HandlerPcpDownloadArquivos,
    HandlerPcpOrcamentoEnviar,
)
from app.servicos.pcp.payload import _consulta
from tests.apoio import MotorFalso, configuracao

# --- Dublês ------------------------------------------------------------------


def _erp(roteador, **config):
    relogio = SimpleNamespace(agora=0.0)

    def agora():
        return relogio.agora

    def dormir(segundos):
        relogio.agora += segundos

    http = httpx.Client(transport=httpx.MockTransport(roteador))
    return ErpClient(configuracao(**config), http=http, relogio=agora, dormir=dormir)


def _roteador(**por_metodo):
    """Roteador por método HTTP que registra o que passou (o login não conta)."""
    vistos = []

    def roteador(request):
        if request.url.path == "/api/v1/auth":
            return httpx.Response(200, json={"sucess": True, "code": 200, "data": {"token": "tok-1"}})
        vistos.append(request)
        resposta = por_metodo.get(request.method)
        if resposta is None:
            raise AssertionError(f"chamada inesperada: {request.method} {request.url}")
        return resposta(request) if callable(resposta) else resposta

    return roteador, vistos


class ConexaoFalsa:
    """Devolve linhas fixas e registra o que foi executado.

    Guardar as declarações é o que permite provar que NENHUMA escrita de domínio
    saiu do worker: nos testes abaixo só aparecem SELECTs.
    """

    def __init__(self, linhas=None, mapeadas=None):
        self._linhas = linhas or []
        self._mapeadas = mapeadas or []
        self.executados = []

    def execute(self, declaracao, parametros=None):
        self.executados.append((declaracao, parametros))
        linhas, mapeadas = self._linhas, self._mapeadas
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(linhas)),
            mappings=lambda: list(mapeadas),
        )


def _item(payload, *, tipo="pcp.orcamento.enviar", tentativa=1, max_tentativas=5):
    return ItemReivindicado(
        id=7, tipo_codigo=tipo, classe="assincrono", destino="erp_wingraph", prioridade=500,
        origem="pcp", status_id=3, grupo_id=None, ordem_no_grupo=None,
        correlation_id=uuid.uuid4(), chave_bloqueio=None, chave_idempotencia=None,
        payload=payload, payload_enviado=None, tentativa=tentativa, max_tentativas=max_tentativas,
        cancelamento_solicitado=False, solicitante_usuario_id=None, lease_expira_em=None,
        criado_em=None,
    )


def _catalogo(tipo_codigo, *, idempotente=False):
    status = {codigo: indice + 1 for indice, codigo in enumerate(CODIGOS_OBRIGATORIOS)}
    tipo = Tipo(codigo=tipo_codigo, nome=tipo_codigo, destino="erp_wingraph", classe_padrao="assincrono",
                prioridade_padrao=500, max_tentativas_padrao=5, timeout_segundos=300,
                concorrencia_maxima=4, idempotente=idempotente, tipo_verificacao_codigo=None)
    return Catalogo(status=status, status_por_id={v: k for k, v in status.items()},
                    tipos={tipo_codigo: tipo})


# --- Orçamento ----------------------------------------------------------------

WEBHOOK = "https://pageflow.teste/api/pcp/webhook/orcamento/7?token=segredo"

# O que o SQL devolve: um orçamento montado, com os ids de origem no
# `codigo_externo` (é isso que `validar_itens` confere).
LINHA_SQL = {"data": {"id_cliente": 10, "itens": [{"codigo_externo": "101,102", "quantidade": 5}]}}


def _payload_orcamento(**sobrescritas):
    payload = {
        "orcamento_id": 700,
        "lote_id": 12,
        "requisicao_id": 40,
        "origem": "escola",
        "cliente_id": 10,
        "vendedor_id": 20,
        "forma_pagamento": 3,
        "ids_origem": [101, 102],
        "data_entrega": "15/10/2026",
        "url_webhook": WEBHOOK,
    }
    payload.update(sobrescritas)
    return payload


def _envelope_orcamento(**dados):
    return {"success": True, "code": 200, "data": dados}


class TestOrcamentoPreparo(unittest.TestCase):
    def test_origem_escola_usa_o_sql_agrupado(self):
        roteador, _ = _roteador(POST=httpx.Response(200, json=_envelope_orcamento(id_requisicao=9)))
        conexao = ConexaoFalsa(linhas=[LINHA_SQL])

        preparo = HandlerPcpOrcamentoEnviar(_erp(roteador)).preparar(conexao, _item(_payload_orcamento()))

        declaracao, parametros = conexao.executados[0]
        self.assertEqual(str(declaracao), str(_consulta("orcamento_agrupado.sql", "pedido_distribuicao_ids")))
        self.assertEqual(parametros["pedido_distribuicao_ids"], [101, 102])
        self.assertEqual(parametros["data_entrega"], "15/10/2026")
        # O corpo real vai para `payload_enviado`, sem a url_webhook: ela carrega
        # o token do webhook e apareceria na tela da fila.
        self.assertEqual(preparo.payload_enviado["identifier"], "PageFlow")
        self.assertTrue(preparo.payload_enviado["assincrono"])
        self.assertEqual(preparo.payload_enviado["url_webhook"], "[omitida]")

    def test_item_antigo_com_modo_agrupamento_e_aceito_e_vai_para_o_agrupado(self):
        # Itens enfileirados antes de 2026-10-07 ainda trazem o campo, inclusive
        # `unidade`. Recusá-los seria falha DEFINITIVA para um envio válido.
        roteador, _ = _roteador(POST=httpx.Response(200, json=_envelope_orcamento(id_requisicao=9)))
        conexao = ConexaoFalsa(linhas=[LINHA_SQL])

        HandlerPcpOrcamentoEnviar(_erp(roteador)).preparar(
            conexao, _item(_payload_orcamento(modo_agrupamento="unidade")))

        self.assertEqual(str(conexao.executados[0][0]),
                         str(_consulta("orcamento_agrupado.sql", "pedido_distribuicao_ids")))

    def test_origem_integracao_usa_o_sql_de_integracao_e_o_outro_parametro(self):
        roteador, _ = _roteador(POST=httpx.Response(200, json=_envelope_orcamento(id_requisicao=9)))
        conexao = ConexaoFalsa(linhas=[LINHA_SQL])

        HandlerPcpOrcamentoEnviar(_erp(roteador)).preparar(conexao, _item(_payload_orcamento(
            origem="integracao")))

        declaracao, parametros = conexao.executados[0]
        self.assertEqual(str(declaracao),
                         str(_consulta("orcamento_integracao.sql", "integra_pedido_produto_ids")))
        self.assertEqual(parametros["integra_pedido_produto_ids"], [101, 102])

    def test_sem_webhook_o_envio_vai_sincrono(self):
        roteador, _ = _roteador(POST=httpx.Response(200, json=_envelope_orcamento(id_orcamento=555)))
        conexao = ConexaoFalsa(linhas=[LINHA_SQL])

        preparo = HandlerPcpOrcamentoEnviar(_erp(roteador)).preparar(
            conexao, _item(_payload_orcamento(url_webhook=None)))

        self.assertNotIn("assincrono", preparo.payload_enviado)
        self.assertNotIn("url_webhook", preparo.payload_enviado)


class TestOrcamentoPayloadInvalido(unittest.TestCase):
    """Payload sem o mínimo é falha DEFINITIVA e NENHUMA chamada sai."""

    def _falha(self, payload, esperado_em: str):
        roteador, vistos = _roteador()
        handler = HandlerPcpOrcamentoEnviar(_erp(roteador))
        item = _item(payload)
        conexao = ConexaoFalsa(linhas=[LINHA_SQL])

        preparo = handler.preparar(conexao, item)
        desfecho = handler.interpretar(item, preparo.chamar())

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertIn(esperado_em, desfecho.erro)
        self.assertEqual(vistos, [], "payload inválido não pode gerar chamada ao ERP")
        return desfecho

    def test_sem_ids_de_origem(self):
        self._falha(_payload_orcamento(ids_origem=[]), "ids_origem")

    def test_origem_desconhecida(self):
        self._falha(_payload_orcamento(origem="parceiro"), "Origem do orçamento desconhecida")

    def test_data_entrega_fora_do_formato(self):
        self._falha(_payload_orcamento(data_entrega="2026-10-15"), "DD/MM/YYYY")

    def test_id_que_nao_e_id(self):
        self._falha(_payload_orcamento(ids_origem=[101, "x"]), "não é id")

    def test_cadastro_incompleto_do_sql_tambem_e_definitivo(self):
        # `validar_cabecalho` no SQL antigo: unidade sem vendedor cadastrado.
        desfecho = self._falha(_payload_orcamento(vendedor_id=None), "vendedor")
        self.assertEqual(desfecho.erro_codigo, "ORCAMENTO_INCOMPLETO")

    def test_item_que_o_sql_descartou_nao_passa_calado(self):
        # O SQL montou só o 101: o 102 ficaria fora do orçamento sem aviso.
        roteador, vistos = _roteador()
        handler = HandlerPcpOrcamentoEnviar(_erp(roteador))
        item = _item(_payload_orcamento())
        conexao = ConexaoFalsa(linhas=[{"data": {"itens": [{"codigo_externo": "101"}]}}])

        desfecho = handler.interpretar(item, handler.preparar(conexao, item).chamar())

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertIn("[102]", desfecho.erro)
        self.assertEqual(vistos, [])


class TestOrcamentoInterpretacao(unittest.TestCase):
    def _desfecho(self, resposta, payload=None):
        roteador, vistos = _roteador(POST=resposta)
        handler = HandlerPcpOrcamentoEnviar(_erp(roteador))
        item = _item(payload or _payload_orcamento())
        preparo = handler.preparar(ConexaoFalsa(linhas=[LINHA_SQL]), item)
        return handler.interpretar(item, preparo.chamar()), vistos

    def test_ack_assincrono_fica_aguardando_callback(self):
        desfecho, _ = self._desfecho(httpx.Response(200, json=_envelope_orcamento(id_requisicao=4321)))

        # O ponto da fase: aceito não é concluído. Quem conclui é o webhook.
        self.assertIs(desfecho.estado, Estado.AGUARDANDO_CALLBACK)
        self.assertEqual(desfecho.resultado["id_requisicao"], 4321)
        self.assertIsNone(desfecho.resultado["id_orcamento"])
        self.assertEqual(desfecho.resultado["modo_envio"], "assincrono")
        self.assertEqual(desfecho.resultado["resposta"]["data"]["id_requisicao"], 4321)

    def test_ack_assincrono_sem_id_requisicao_e_incerto(self):
        desfecho, _ = self._desfecho(httpx.Response(200, json={"success": True, "data": {}}))

        self.assertIs(desfecho.estado, Estado.INCERTO)
        self.assertEqual(desfecho.erro_codigo, "ERP_ACK_SEM_REQUISICAO")

    def test_resposta_sincrona_com_id_orcamento_conclui(self):
        desfecho, _ = self._desfecho(
            httpx.Response(200, json=_envelope_orcamento(id_orcamento=555)),
            payload=_payload_orcamento(url_webhook=None))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_orcamento"], 555)
        self.assertEqual(desfecho.resultado["modo_envio"], "sincrono")

    def test_resultado_completo_em_chamada_assincrona_conclui_sem_esperar_webhook(self):
        desfecho, _ = self._desfecho(
            httpx.Response(200, json=_envelope_orcamento(id_orcamento=555, id_requisicao=4321)))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_orcamento"], 555)
        self.assertEqual(desfecho.resultado["id_requisicao"], 4321)

    def test_sucesso_sem_id_orcamento_e_incerto(self):
        desfecho, _ = self._desfecho(
            httpx.Response(200, json={"success": True, "data": {}}),
            payload=_payload_orcamento(url_webhook=None))

        self.assertIs(desfecho.estado, Estado.INCERTO)
        self.assertEqual(desfecho.erro_codigo, "ERP_SUCESSO_SEM_ID")

    def test_429_e_retentavel(self):
        desfecho, _ = self._desfecho(httpx.Response(429, json={"success": False, "message": "calma"}))

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "ERP_HTTP_429")

    def test_400_e_definitivo(self):
        desfecho, _ = self._desfecho(httpx.Response(400, json={"success": False, "message": "produto inválido"}))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertIn("produto inválido", desfecho.erro)

    def test_500_depois_de_receber_a_escrita_e_incerto(self):
        desfecho, _ = self._desfecho(httpx.Response(500, json={"success": False, "message": "boom"}))

        self.assertIs(desfecho.estado, Estado.INCERTO)
        self.assertEqual(desfecho.erro_codigo, "ERP_HTTP_500")


class TestNaoIdempotenteNaoRetentaSozinho(unittest.TestCase):
    """O motor não pode transformar incerto em nova chamada nestes dois tipos:
    orçamento e aprovação duplicados no ERP só saem de lá à mão."""

    def _motor(self, catalogo):
        return Motor(MotorFalso(), catalogo, registry_padrao(object()), configuracao(
            FILA_BACKOFF_SINCRONO_BASE=2.0, FILA_BACKOFF_SINCRONO_TETO=8.0,
            FILA_BACKOFF_ASSINCRONO_BASE=15.0, FILA_BACKOFF_ASSINCRONO_TETO=900.0,
            FILA_LEASE_MARGEM_SEGUNDOS=30), "worker-teste")

    def test_resposta_ilegivel_do_post_fica_incerta_e_nao_e_repetida(self):
        # 2xx sem JSON: o `ErpClient` levanta `ResultadoIncerto` porque a escrita
        # pode ter acontecido.
        roteador, vistos = _roteador(POST=httpx.Response(200, text="<html>erro</html>"))
        handler = HandlerPcpOrcamentoEnviar(_erp(roteador))
        item = _item(_payload_orcamento())
        preparo = handler.preparar(ConexaoFalsa(linhas=[LINHA_SQL]), item)

        desfecho = self._motor(_catalogo("pcp.orcamento.enviar"))._chamar(
            item, handler, preparo, 30.0)

        self.assertIs(desfecho.estado, Estado.INCERTO)
        self.assertEqual(desfecho.erro_codigo, "RESULTADO_INCERTO")
        self.assertEqual(len(vistos), 1, "a chamada não pode ser repetida")

    def test_verificacao_do_pcp_nao_decide_e_o_item_segue_incerto(self):
        # Enquanto a Bremen não confirmar a consulta por codigo_externo, o
        # incerto vai para decisão humana em vez de virar retentativa.
        roteador, vistos = _roteador()
        motor = self._motor(_catalogo("pcp.orcamento.enviar"))
        incerto = Desfecho.incerto("sem confirmação", "RESULTADO_INCERTO")

        resolvido = motor._verificar(_item(_payload_orcamento()),
                                    HandlerPcpOrcamentoEnviar(_erp(roteador)), incerto, 30.0)

        self.assertIs(resolvido, incerto)
        self.assertEqual(vistos, [], "sem verificador confirmado não se consulta o ERP")


# --- Aprovação ----------------------------------------------------------------

def _payload_aprovacao(**sobrescritas):
    payload = {
        "aprovacao_id": 900,
        "orcamento_id": 700,
        "id_orcamento": 555,
        "gerar_op": True,
        "itens_aprovados": [{"id": 1, "data_saida": "20/10/2026"}, {"id": 2}],
        "url_webhook": WEBHOOK,
    }
    payload.update(sobrescritas)
    return payload


# O que `sql/aprovacao.sql` devolve: o `data` do POST, montado pela aprovação.
DADOS_APROVACAO = {
    "id_orcamento": 555,
    "gerar_op": True,
    "itens": [{"id": 1, "data_entrega": "2026-10-20T18:00:00.000-03:00"}, {"id": 2}],
}


def _conexao_aprovacao(**sobrescritas):
    return ConexaoFalsa(linhas=[{**DADOS_APROVACAO, **sobrescritas}])


def _proposta(status_por_item):
    return {"success": True, "data": [{"itens": [
        {"id": id_item, "status": status} for id_item, status in status_por_item.items()]}]}


class TestAprovacao(unittest.TestCase):
    def _executar(self, payload, *, get=None, post=None, conexao=None):
        por_metodo = {}
        if get is not None:
            por_metodo["GET"] = get
        if post is not None:
            por_metodo["POST"] = post
        roteador, vistos = _roteador(**por_metodo)
        handler = HandlerPcpAprovacaoEnviar(_erp(roteador))
        item = _item(payload, tipo="pcp.aprovacao.enviar")
        preparo = handler.preparar(conexao or _conexao_aprovacao(), item)
        return handler.interpretar(item, preparo.chamar()), vistos, preparo

    def test_proposta_ja_aprovada_no_erp_nao_gera_novo_post(self):
        desfecho, vistos, _ = self._executar(
            _payload_aprovacao(),
            get=httpx.Response(200, json=_proposta({1: "Confirmada", 2: "confirmada"})))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertTrue(desfecho.resultado["ja_aprovada"])
        self.assertIsNone(desfecho.resultado["id_requisicao"])
        self.assertEqual([requisicao.method for requisicao in vistos], ["GET"])

    def test_item_ainda_nao_confirmado_segue_para_o_post(self):
        desfecho, vistos, preparo = self._executar(
            _payload_aprovacao(),
            get=httpx.Response(200, json=_proposta({1: "Confirmada", 2: "Aberta"})),
            post=httpx.Response(200, json={"success": True, "data": {"id_requisicao": 77}}))

        self.assertIs(desfecho.estado, Estado.AGUARDANDO_CALLBACK)
        self.assertEqual(desfecho.resultado["id_requisicao"], 77)
        self.assertFalse(desfecho.resultado["ja_aprovada"])
        self.assertEqual([requisicao.method for requisicao in vistos], ["GET", "POST"])
        self.assertEqual(preparo.payload_enviado["data"]["gerar_op"], True)
        self.assertEqual(preparo.payload_enviado["url_webhook"], "[omitida]")

    def test_resposta_sincrona_com_lista_de_propostas_conclui(self):
        desfecho, _, _ = self._executar(
            _payload_aprovacao(url_webhook=None),
            get=httpx.Response(200, json=_proposta({1: "Aberta", 2: "Aberta"})),
            post=httpx.Response(200, json={"success": True, "data": [{"id_proposta": 9, "ops": []}]}))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["modo_envio"], "sincrono")
        self.assertIsNotNone(desfecho.resultado["consulta_previa"])

    def test_proposta_nao_encontrada_segue_para_o_post(self):
        # Resposta real do ERP (orçamentos 19458/19459, 2026-10-02): HTTP 400 com
        # `data` objeto. Antes estourava AttributeError e o item ia a `incerto`.
        nao_encontrada = {"sucess": False, "code": 400, "message": "Solicitação inválida",
                          "data": {"error": "Proposta não encontrada"}}
        desfecho, vistos, _ = self._executar(
            _payload_aprovacao(),
            get=httpx.Response(400, json=nao_encontrada),
            post=httpx.Response(200, json={"success": True, "data": {"id_requisicao": 78}}))

        self.assertIs(desfecho.estado, Estado.AGUARDANDO_CALLBACK)
        self.assertFalse(desfecho.resultado["ja_aprovada"])
        self.assertEqual(desfecho.resultado["consulta_previa"], nao_encontrada)
        self.assertEqual([requisicao.method for requisicao in vistos], ["GET", "POST"])

    def test_proposta_unica_como_objeto_ainda_detecta_aprovacao(self):
        proposta = _proposta({1: "Confirmada", 2: "Confirmada"})
        proposta["data"] = proposta["data"][0]
        desfecho, vistos, _ = self._executar(_payload_aprovacao(), get=httpx.Response(200, json=proposta))

        self.assertTrue(desfecho.resultado["ja_aprovada"])
        self.assertEqual([requisicao.method for requisicao in vistos], ["GET"])

    def test_consulta_previa_indisponivel_e_retentavel_sem_enviar_nada(self):
        def cai(request):
            raise httpx.ConnectError("sem rota", request=request)

        roteador, vistos = _roteador(GET=cai)
        handler = HandlerPcpAprovacaoEnviar(_erp(roteador))
        item = _item(_payload_aprovacao(), tipo="pcp.aprovacao.enviar")

        preparo = handler.preparar(_conexao_aprovacao(), item)
        desfecho = handler.interpretar(item, preparo.chamar())

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "CONSULTA_PREVIA_INDISPONIVEL")
        self.assertEqual([requisicao.method for requisicao in vistos].count("POST"), 0)

    def test_sem_id_orcamento_e_falha_definitiva(self):
        roteador, vistos = _roteador()
        handler = HandlerPcpAprovacaoEnviar(_erp(roteador))
        item = _item(_payload_aprovacao(), tipo="pcp.aprovacao.enviar")

        preparo = handler.preparar(_conexao_aprovacao(id_orcamento=None), item)
        desfecho = handler.interpretar(item, preparo.chamar())

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "SEM_ID_ORCAMENTO")
        self.assertEqual(vistos, [])

    def test_sem_itens_aprovados_e_falha_definitiva(self):
        roteador, vistos = _roteador()
        handler = HandlerPcpAprovacaoEnviar(_erp(roteador))
        item = _item(_payload_aprovacao(), tipo="pcp.aprovacao.enviar")

        desfecho = handler.interpretar(
            item, handler.preparar(_conexao_aprovacao(itens=[]), item).chamar())

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "SEM_ITENS_APROVADOS")
        self.assertEqual(vistos, [])

    def test_aprovacao_inexistente_no_banco_e_falha_definitiva(self):
        roteador, vistos = _roteador()
        handler = HandlerPcpAprovacaoEnviar(_erp(roteador))
        item = _item(_payload_aprovacao(), tipo="pcp.aprovacao.enviar")

        desfecho = handler.interpretar(item, handler.preparar(ConexaoFalsa(), item).chamar())

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "APROVACAO_NAO_ENCONTRADA")
        self.assertEqual(vistos, [])

    def test_corpo_sai_do_sql_e_nao_do_payload(self):
        # O payload ainda traz `itens_aprovados`/`gerar_op`/`id_orcamento` do
        # PageFlow, mas quem manda é o SQL, consultado pela aprovação.
        conexao = _conexao_aprovacao(
            id_orcamento=999, gerar_op=False,
            itens=[{"id": 7, "data_entrega": "2026-11-01T18:00:00.000-03:00",
                    "entregas": [{"quantidade": 3, "id_cliente": 1}]}])
        _, vistos, preparo = self._executar(
            _payload_aprovacao(),
            get=httpx.Response(200, json=_proposta({7: "Aberta"})),
            post=httpx.Response(200, json={"success": True, "data": {"id_requisicao": 80}}),
            conexao=conexao)

        self.assertEqual(conexao.executados[0][1], {"aprovacao_id": 900})
        dados = preparo.payload_enviado["data"]
        self.assertEqual(dados["id_orcamento"], 999)
        self.assertFalse(dados["gerar_op"])
        self.assertEqual(dados["itens"][0]["entregas"], [{"quantidade": 3, "id_cliente": 1}])
        self.assertIn("/proposta/aprovar", str(vistos[-1].url))

    def test_erp_recusa_a_aprovacao(self):
        desfecho, _, _ = self._executar(
            _payload_aprovacao(),
            get=httpx.Response(200, json=_proposta({1: "Aberta", 2: "Aberta"})),
            post=httpx.Response(400, json={"success": False, "message": "proposta vencida"}))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertIn("proposta vencida", desfecho.erro)


# --- Download dos arquivos da OP ----------------------------------------------

class BaixadorFalso:
    """Escreve um arquivo de verdade (o handler publica pastas) sem rede."""

    def __init__(self, falhar_em=()):
        self._falhar_em = set(falhar_em)
        self.baixados = []

    def baixar(self, url, destino):
        if url in self._falhar_em:
            raise RuntimeError("falha ao baixar (403)")
        with open(destino, "wb") as arquivo:
            arquivo.write(b"%PDF-1.4 conteudo")
        self.baixados.append(url)
        return os.path.getsize(destino)


def _linhas_arquivos():
    return [
        {"id_op": 5001, "pedido_distribuicao_id": 101, "integra_pedido_produto_id": None,
         "arquivo_pdf_id": 71, "arquivo_nome": "miolo.pdf", "url": "https://blob/miolo.pdf",
         "tipo_arquivo": "miolo", "chave_arquivo": "71", "pasta": "Escola Teste"},
        {"id_op": 5001, "pedido_distribuicao_id": 102, "integra_pedido_produto_id": None,
         "arquivo_pdf_id": 72, "arquivo_nome": "capa.pdf", "url": "https://blob/capa.pdf",
         "tipo_arquivo": "capa", "chave_arquivo": "72", "pasta": "Escola Teste"},
    ]


class TestDownloadArquivos(unittest.TestCase):
    def _executar(self, linhas, *, baixador=None, payload=None, tentativa=1, max_tentativas=5):
        baixador = baixador or BaixadorFalso()
        item = _item(payload or {"aprovacao_id": 900, "orcamento_id": 700, "id_orcamento": 555},
                     tipo="pcp.download_arquivos", tentativa=tentativa, max_tentativas=max_tentativas)
        with tempfile.TemporaryDirectory() as base:
            handler = HandlerPcpDownloadArquivos(baixador, base, dormir=lambda _: None)
            conexao = ConexaoFalsa(mapeadas=linhas)
            preparo = handler.preparar(conexao, item)
            desfecho = handler.interpretar(item, preparo.chamar())
            publicados = sorted(
                os.path.relpath(os.path.join(raiz, nome), base)
                for raiz, _, nomes in os.walk(base) for nome in nomes
            )
        return desfecho, preparo, conexao, publicados

    def test_baixa_publica_e_devolve_as_linhas_de_downloads_bremen(self):
        desfecho, preparo, conexao, publicados = self._executar(_linhas_arquivos())

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertTrue(desfecho.resultado["sucesso"])
        self.assertEqual(desfecho.resultado["total_arquivos"], 2)
        self.assertEqual(desfecho.resultado["erros"], [])
        self.assertEqual(publicados, [
            os.path.join("Escola Teste", "5001", "capa.pdf"),
            os.path.join("Escola Teste", "5001", "miolo.pdf"),
        ])
        # A gravação de `downloads_bremen` deixou de ser do worker: as linhas
        # saem no resultado para o PageFlow inserir.
        self.assertEqual([linha["arquivo_pdf_id"] for linha in desfecho.resultado["arquivos"]], [71, 72])
        self.assertEqual({linha["id_ops"] for linha in desfecho.resultado["arquivos"]}, {5001})
        self.assertEqual(preparo.payload_enviado["ops"], [5001])
        # Uma consulta só, e de leitura: nenhuma escrita de domínio saiu daqui.
        self.assertEqual(len(conexao.executados), 1)
        self.assertIn("SELECT", str(conexao.executados[0][0]).upper())

    def test_aprovacao_sem_arquivo_e_falha_definitiva(self):
        desfecho, _, _, _ = self._executar([])

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "SEM_ARQUIVOS")

    def test_falha_parcial_com_tentativa_sobrando_e_retentada(self):
        desfecho, _, _, publicados = self._executar(
            _linhas_arquivos(), baixador=BaixadorFalso(falhar_em={"https://blob/capa.pdf"}))

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "DOWNLOAD_PARCIAL")
        # O que desceu fica publicado: retentar completa, não recomeça.
        self.assertEqual(publicados, [os.path.join("Escola Teste", "5001", "miolo.pdf")])

    def test_falha_parcial_na_ultima_tentativa_fecha_com_a_lista_de_erros(self):
        desfecho, _, _, _ = self._executar(
            _linhas_arquivos(), baixador=BaixadorFalso(falhar_em={"https://blob/capa.pdf"}),
            tentativa=5, max_tentativas=5)

        # `falhar`/`dlq` não guardam resultado; fechar assim preserva a lista de
        # quais arquivos faltaram, que é o que a tela do PageFlow mostra.
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertFalse(desfecho.resultado["sucesso"])
        self.assertEqual(len(desfecho.resultado["erros"]), 1)
        self.assertEqual(desfecho.resultado["total_arquivos"], 1)

    def test_payload_sem_aprovacao_e_falha_definitiva(self):
        desfecho, _, conexao, _ = self._executar(_linhas_arquivos(), payload={"orcamento_id": 700})

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(conexao.executados, [], "sem aprovacao_id não se consulta nada")


class TestRegistroDosTiposDePcp(unittest.TestCase):
    def test_os_dois_tipos_de_escrita_sempre_entram(self):
        registry = registry_padrao(object())

        self.assertIsInstance(registry.obter("pcp.orcamento.enviar"), HandlerPcpOrcamentoEnviar)
        self.assertIsInstance(registry.obter("pcp.aprovacao.enviar"), HandlerPcpAprovacaoEnviar)

    def test_download_entra_so_com_baixador_e_pasta(self):
        sem_pasta = registry_padrao(object(), baixador=BaixadorFalso(), pasta_download="")
        self.assertIsNone(sem_pasta.obter("pcp.download_arquivos"))

        com_pasta = registry_padrao(object(), baixador=BaixadorFalso(), pasta_download="C:/producao")
        self.assertIsInstance(com_pasta.obter("pcp.download_arquivos"), HandlerPcpDownloadArquivos)


if __name__ == "__main__":
    unittest.main()
