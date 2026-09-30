"""Handlers de cliente (Fase 2a): preparar, interpretar e verificar.

Sem banco e sem rede: o ERP é um `httpx.MockTransport`, como em `test_erp.py`.
Nenhuma chamada real ao ERP em teste nenhum.
"""

import unittest
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import httpx

from deskflow2.clientes.erp import ErpClient
from deskflow2.fila.catalogo import CODIGOS_OBRIGATORIOS, Catalogo, Tipo
from deskflow2.fila.modelos import Desfecho, Estado, ItemReivindicado
from deskflow2.fila.motor import Motor
from deskflow2.fila.registry import Registry, registry_padrao
from deskflow2.handlers import HandlerClienteAtualizar, HandlerClienteConsultar, HandlerClienteCriar
from deskflow2.handlers.comum import PayloadInvalido
from tests.apoio import MotorFalso, configuracao

DOCUMENTO = "12345678000199"

CLIENTE_ERP = {
    "id_cliente": 4242,
    "tipo": 1,
    "nome": "Escola Teste",
    "razao_social": "Escola Teste LTDA",
    "cnpj": "12.345.678/0001-99",
    "email": "contato@escola.teste",
}


def _erp(roteador, **config):
    """ErpClient com transporte falso e sem espera de verdade."""
    relogio = SimpleNamespace(agora=0.0)

    def agora():
        return relogio.agora

    def dormir(segundos):
        relogio.agora += segundos

    http = httpx.Client(transport=httpx.MockTransport(roteador))
    return ErpClient(configuracao(**config), http=http, relogio=agora, dormir=dormir)


def _login(request):
    return httpx.Response(200, json={"sucess": True, "code": 200, "data": {"token": "tok-1"}})


def _roteador(**por_metodo):
    """Um roteador que despacha por método HTTP e registra o que passou."""
    vistos = []

    def roteador(request):
        if request.url.path == "/api/v1/auth":
            return _login(request)
        vistos.append(request)
        resposta = por_metodo.get(request.method)
        if resposta is None:
            raise AssertionError(f"chamada inesperada: {request.method} {request.url}")
        return resposta(request) if callable(resposta) else resposta

    return roteador, vistos


def _item(payload, *, tipo="cliente.criar", classe="sincrono", tentativa=1, max_tentativas=2):
    return ItemReivindicado(
        id=1, tipo_codigo=tipo, classe=classe, destino="erp_wingraph", prioridade=900,
        origem="teste", status_id=3, grupo_id=None, ordem_no_grupo=None,
        correlation_id=uuid.uuid4(), chave_bloqueio=f"cliente:{DOCUMENTO}",
        chave_idempotencia=f"{tipo}:{DOCUMENTO}", payload=payload, payload_enviado=None,
        tentativa=tentativa, max_tentativas=max_tentativas, cancelamento_solicitado=False,
        solicitante_usuario_id=None, lease_expira_em=None, criado_em=None,
    )


def _executar(handler, item):
    """O que o motor faz com um item, sem o motor: preparar -> chamar -> interpretar."""
    preparo = handler.preparar(None, item)
    return handler.interpretar(item, preparo.chamar()), preparo


class TestConsultar(unittest.TestCase):
    def test_busca_por_documento_e_devolve_os_clientes(self):
        roteador, vistos = _roteador(GET=httpx.Response(
            200, json={"success": True, "code": 200, "data": [CLIENTE_ERP], "metadata": {"pages": 1}}))
        handler = HandlerClienteConsultar(_erp(roteador))

        desfecho, preparo = _executar(handler, _item({"documento": DOCUMENTO}, tipo="cliente.consultar"))

        self.assertEqual(vistos[0].method, "GET")
        self.assertEqual(vistos[0].url.params["cpfcnpj"], DOCUMENTO)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["clientes"], [CLIENTE_ERP])
        self.assertEqual(desfecho.resultado["total"], 1)
        self.assertEqual(preparo.payload_enviado["params"], {"cpfcnpj": DOCUMENTO})

    def test_busca_por_id_e_por_pagina(self):
        roteador, vistos = _roteador(GET=httpx.Response(200, json={"success": True, "data": []}))
        handler = HandlerClienteConsultar(_erp(roteador))

        desfecho, _ = _executar(handler, _item({"id_cliente": 7, "page": 3}, tipo="cliente.consultar"))

        self.assertEqual(vistos[0].url.params["id"], "7")
        self.assertEqual(vistos[0].url.params["page"], "3")
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["clientes"], [])

    def test_sem_criterio_e_falha_definitiva_sem_chamar_o_erp(self):
        roteador, vistos = _roteador()
        handler = HandlerClienteConsultar(_erp(roteador))

        preparo = handler.preparar(None, _item({}, tipo="cliente.consultar"))
        bruto = preparo.chamar()

        self.assertIsInstance(bruto, PayloadInvalido)
        self.assertEqual(vistos, [], "payload inválido não pode chegar ao ERP")
        self.assertIs(handler.interpretar(_item({}), bruto).estado, Estado.FALHOU)

    def test_leitura_nunca_pede_verificacao(self):
        roteador, _ = _roteador()
        handler = HandlerClienteConsultar(_erp(roteador))
        self.assertIsNone(handler.verificar(None, _item({"documento": DOCUMENTO})))


class TestCriar(unittest.TestCase):
    def _payload(self):
        return {"cliente": {"cnpj": DOCUMENTO, "nome": "Escola Teste"}, "documento": DOCUMENTO}

    def test_caminho_feliz_devolve_id_cliente(self):
        roteador, vistos = _roteador(POST=httpx.Response(
            200, json={"success": True, "code": 200, "data": {"id_cliente": 4242}}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho, preparo = _executar(handler, _item(self._payload()))

        import json
        corpo = json.loads(vistos[0].content)
        self.assertEqual(vistos[0].method, "POST")
        self.assertEqual(corpo["identifier"], "PageFlow")
        self.assertEqual(corpo["data"]["cnpj"], DOCUMENTO)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        self.assertEqual(preparo.payload_enviado["caminho"], "/api/v1/cliente")

    def test_erro_de_negocio_e_falha_definitiva(self):
        # 400 com mensagem do ERP: processou e recusou. Não adianta repetir, e
        # por desenho não consome o orçamento de tentativas.
        roteador, _ = _roteador(POST=httpx.Response(
            400, json={"success": False, "message": "CNPJ inválido"}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload()))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_HTTP_400")
        self.assertIn("CNPJ inválido", desfecho.erro)

    def test_sucesso_com_success_false_tambem_e_falha_definitiva(self):
        roteador, _ = _roteador(POST=httpx.Response(
            200, json={"sucess": False, "code": 422, "message": "Cliente já cadastrado"}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload()))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertIn("Cliente já cadastrado", desfecho.erro)

    def test_502_e_retentavel(self):
        roteador, _ = _roteador(POST=httpx.Response(502, text="bad gateway"))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload()))

        self.assertIs(desfecho.estado, Estado.RETENTAR)

    def test_500_em_escrita_e_incerto(self):
        roteador, _ = _roteador(POST=httpx.Response(500, json={"message": "erro interno"}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload()))

        self.assertIs(desfecho.estado, Estado.INCERTO)

    def test_sucesso_sem_id_cliente_e_incerto(self):
        roteador, _ = _roteador(POST=httpx.Response(200, json={"success": True, "data": {}}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload()))

        self.assertIs(desfecho.estado, Estado.INCERTO)
        self.assertEqual(desfecho.erro_codigo, "ERP_SUCESSO_SEM_ID")

    def test_payload_sem_documento_nao_chega_ao_erp(self):
        roteador, vistos = _roteador()
        handler = HandlerClienteCriar(_erp(roteador))

        preparo = handler.preparar(None, _item({"cliente": {"nome": "Sem documento"}}))
        desfecho = handler.interpretar(_item({}), preparo.chamar())

        self.assertEqual(vistos, [])
        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "PAYLOAD_INVALIDO")

    # --- verificação de resultado incerto -----------------------------------

    def test_verificar_encontra_o_cliente_e_fecha_como_concluido(self):
        roteador, vistos = _roteador(GET=httpx.Response(
            200, json={"success": True, "data": [CLIENTE_ERP]}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho = handler.verificar(None, _item(self._payload()))

        self.assertEqual(vistos[0].method, "GET", "a verificação precisa ser leitura")
        self.assertEqual(vistos[0].url.params["cpfcnpj"], DOCUMENTO)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        self.assertEqual(desfecho.resultado["verificado_por"], "cliente.consultar")

    def test_verificar_nao_encontra_e_devolve_o_item_para_a_fila(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": []}))
        handler = HandlerClienteCriar(_erp(roteador))

        desfecho = handler.verificar(None, _item(self._payload()))

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "VERIFICACAO_NAO_CRIADO")

    def test_verificar_ignora_cliente_de_outro_documento(self):
        outro = {**CLIENTE_ERP, "cnpj": "99.999.999/0001-00"}
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [outro]}))
        handler = HandlerClienteCriar(_erp(roteador))

        self.assertIs(handler.verificar(None, _item(self._payload())).estado, Estado.RETENTAR)

    def test_consulta_que_falha_mantem_o_item_incerto(self):
        # Consulta não concluída NÃO é prova de ausência: é o falso negativo
        # silencioso do cliente legado do PageFlow, que aqui não se repete.
        roteador, _ = _roteador(GET=httpx.Response(500, json={"message": "indisponível"}))
        handler = HandlerClienteCriar(_erp(roteador))

        self.assertIsNone(handler.verificar(None, _item(self._payload())))

    def test_verificar_sem_documento_nao_decide(self):
        roteador, vistos = _roteador()
        handler = HandlerClienteCriar(_erp(roteador))

        self.assertIsNone(handler.verificar(None, _item({"cliente": {"nome": "x"}})))
        self.assertEqual(vistos, [])


class TestAtualizar(unittest.TestCase):
    def _payload(self, **extras):
        cliente = {"cnpj": DOCUMENTO, "nome": "Escola Teste", "razao_social": "Escola Teste LTDA"}
        cliente.update(extras)
        return {"id_cliente": 4242, "cliente": cliente, "documento": DOCUMENTO,
                "extras": {"observacao": "nota interna"}}

    def test_caminho_feliz_envia_patch_com_envelope(self):
        import json

        roteador, vistos = _roteador(PATCH=httpx.Response(
            200, json={"success": True, "code": 200, "data": {"id_cliente": 4242}}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho, preparo = _executar(handler, _item(self._payload(), tipo="cliente.atualizar"))

        corpo = json.loads(vistos[0].content)
        self.assertEqual(vistos[0].method, "PATCH")
        self.assertEqual(corpo["identifier"], "PageFlow")
        self.assertEqual(corpo["data"]["id_cliente"], 4242)
        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        self.assertEqual(preparo.payload_enviado["metodo"], "PATCH")

    def test_sem_id_cliente_e_falha_definitiva_sem_chamar_o_erp(self):
        roteador, vistos = _roteador()
        handler = HandlerClienteAtualizar(_erp(roteador))

        preparo = handler.preparar(None, _item({"cliente": {"cnpj": DOCUMENTO}}, tipo="cliente.atualizar"))
        desfecho = handler.interpretar(_item({}), preparo.chamar())

        self.assertEqual(vistos, [])
        self.assertIs(desfecho.estado, Estado.FALHOU)

    def test_erro_de_negocio_e_falha_definitiva(self):
        roteador, _ = _roteador(PATCH=httpx.Response(
            404, json={"success": False, "message": "Cliente não encontrado"}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho, _ = _executar(handler, _item(self._payload(), tipo="cliente.atualizar"))

        self.assertIs(desfecho.estado, Estado.FALHOU)
        self.assertEqual(desfecho.erro_codigo, "ERP_HTTP_404")

    def test_verificar_conclui_quando_o_erp_ja_reflete_a_alteracao(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho = handler.verificar(None, _item(self._payload(), tipo="cliente.atualizar"))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)

    def test_verificar_devolve_a_fila_quando_o_cadastro_diverge(self):
        # Existir não prova que o PATCH valeu: o que decide é o ERP refletir o
        # que foi enviado.
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho = handler.verificar(
            None, _item(self._payload(nome="Nome novo"), tipo="cliente.atualizar"))

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "VERIFICACAO_NAO_APLICADA")
        self.assertIn("nome", desfecho.erro)

    def test_verificar_devolve_a_fila_quando_o_cliente_nao_existe(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": []}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho = handler.verificar(None, _item(self._payload(), tipo="cliente.atualizar"))

        self.assertIs(desfecho.estado, Estado.RETENTAR)
        self.assertEqual(desfecho.erro_codigo, "VERIFICACAO_NAO_ENCONTRADO")

    def test_verificar_aceita_documento_formatado_e_numero_como_texto(self):
        registro = {**CLIENTE_ERP, "suframa": 0}
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [registro]}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho = handler.verificar(
            None, _item(self._payload(suframa="0"), tipo="cliente.atualizar"))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)

    def test_verificar_ignora_contato_e_endereco(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}))
        handler = HandlerClienteAtualizar(_erp(roteador))

        desfecho = handler.verificar(None, _item(
            self._payload(contato=[{"id": 1, "nome": "Alguém"}], endereco=[{"id": 2}]),
            tipo="cliente.atualizar"))

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)


class TestRegistry(unittest.TestCase):
    def test_registry_padrao_resolve_os_tres_tipos_de_cliente(self):
        roteador, _ = _roteador()
        registry = registry_padrao(_erp(roteador))

        self.assertIsInstance(registry.obter("cliente.consultar"), HandlerClienteConsultar)
        self.assertIsInstance(registry.obter("cliente.criar"), HandlerClienteCriar)
        self.assertIsInstance(registry.obter("cliente.atualizar"), HandlerClienteAtualizar)
        self.assertIsNone(registry.obter("vendedor.listar_pagina"),
                          "tipo sem endpoint confirmado não pode ter handler")

    def test_registrar_duplicado_e_recusado(self):
        roteador, _ = _roteador()
        erp = _erp(roteador)
        registry = registry_padrao(erp)
        with self.assertRaises(ValueError):
            registry.registrar("cliente.criar", HandlerClienteCriar(erp))


def _catalogo(tipo_codigo, *, idempotente=False, verificador="cliente.consultar"):
    status = {codigo: indice + 1 for indice, codigo in enumerate(CODIGOS_OBRIGATORIOS)}
    tipo = Tipo(codigo=tipo_codigo, nome=tipo_codigo, destino="erp_wingraph", classe_padrao="sincrono",
                prioridade_padrao=900, max_tentativas_padrao=2, timeout_segundos=30,
                concorrencia_maxima=None, idempotente=idempotente, tipo_verificacao_codigo=verificador)
    return Catalogo(status=status, status_por_id={v: k for k, v in status.items()},
                    tipos={tipo_codigo: tipo})


class _ConexaoDeEventos:
    """Só o suficiente para `registrar_evento` funcionar sem banco."""

    def __init__(self, eventos):
        self._eventos = eventos

    def execute(self, _sql, parametros=None):
        self._eventos.append(parametros or {})
        return SimpleNamespace(rowcount=1)


class _EngineDeEventos(MotorFalso):
    def __init__(self):
        super().__init__()
        self.eventos = []

    @contextmanager
    def begin(self):
        self.transacoes += 1
        yield _ConexaoDeEventos(self.eventos)


class TestMotorChamaAVerificacao(unittest.TestCase):
    """O motor não conhece handler nenhum: ele só liga `tipo_verificacao_codigo`
    ao `verificar()` do handler que o registry devolveu."""

    def _motor(self, registry, catalogo, engine=None):
        return Motor(engine or _EngineDeEventos(), catalogo, registry, configuracao(
            FILA_BACKOFF_SINCRONO_BASE=2.0, FILA_BACKOFF_SINCRONO_TETO=8.0,
            FILA_BACKOFF_ASSINCRONO_BASE=15.0, FILA_BACKOFF_ASSINCRONO_TETO=900.0,
            FILA_LEASE_MARGEM_SEGUNDOS=30), "worker-teste")

    def test_incerto_resolvido_pela_verificacao_vira_concluido(self):
        roteador, _ = _roteador(GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}))
        registry = registry_padrao(_erp(roteador))
        engine = _EngineDeEventos()
        motor = self._motor(registry, _catalogo("cliente.criar"), engine)
        item = _item({"cliente": {"cnpj": DOCUMENTO}, "documento": DOCUMENTO})

        desfecho = motor._verificar(item, registry.obter("cliente.criar"),
                                    Desfecho.incerto("sem confirmação", "RESULTADO_INCERTO"), 30.0)

        self.assertIs(desfecho.estado, Estado.CONCLUIDO)
        self.assertEqual(desfecho.resultado["id_cliente"], 4242)
        # A verificação entra na timeline: é ela que explica um item que fechou
        # sem que a resposta do ERP tenha chegado.
        self.assertEqual([evento["evento"] for evento in engine.eventos], ["verificacao"])
        self.assertEqual(engine.eventos[0]["detalhe"]["verificador"], "cliente.consultar")

    def test_tipo_sem_verificador_declarado_segue_incerto(self):
        roteador, vistos = _roteador(GET=httpx.Response(200, json={"success": True, "data": [CLIENTE_ERP]}))
        registry = registry_padrao(_erp(roteador))
        motor = self._motor(registry, _catalogo("cliente.criar", verificador=None))
        incerto = Desfecho.incerto("sem confirmação", "RESULTADO_INCERTO")

        desfecho = motor._verificar(
            _item({"documento": DOCUMENTO}), registry.obter("cliente.criar"), incerto, 30.0)

        self.assertIs(desfecho, incerto)
        self.assertEqual(vistos, [], "sem tipo_verificacao_codigo não se consulta o destino")

    def test_handler_sem_verificar_segue_incerto(self):
        class SemVerificar:
            def preparar(self, conn, item):
                return None

            def interpretar(self, item, bruto):
                return Desfecho.concluido()

        registry = Registry()
        registry.registrar("cliente.criar", SemVerificar())
        motor = self._motor(registry, _catalogo("cliente.criar"))
        incerto = Desfecho.incerto("sem confirmação", "RESULTADO_INCERTO")

        self.assertIs(motor._verificar(_item({}), registry.obter("cliente.criar"), incerto, 30.0), incerto)


if __name__ == "__main__":
    unittest.main()
