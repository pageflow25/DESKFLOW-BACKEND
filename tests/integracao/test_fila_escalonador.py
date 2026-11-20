"""Escalonador: pool síncrono de 1, pool assíncrono em paralelo, registry vazio.

Nenhuma chamada real ao ERP: o handler de teste só dorme e conta.
"""

import threading
import time
import unittest
from types import SimpleNamespace

from app.fila.escalonador import Escalonador
from app.fila.modelos import Desfecho, Preparo
from app.fila.registry import Registry
from tests.integracao.apoio_banco import TesteDeFila

DURACAO_CHAMADA = 0.35


def _configuracao(destino, **extras):
    padrao = dict(
        FILA_DESTINO=destino,
        FILA_WORKER_ID="",
        FILA_POOL_SINCRONO=1,
        FILA_POOL_ASSINCRONO=4,
        FILA_LOTE_CLAIM=10,
        FILA_JANELA_CLAIM_MULTIPLICADOR=4,
        FILA_LEASE_MARGEM_SEGUNDOS=30,
        FILA_ENVELHECIMENTO_MINUTOS=15,
        FILA_ENVELHECIMENTO_PASSO=10,
        FILA_ENVELHECIMENTO_TETO=690,
        FILA_BACKOFF_SINCRONO_BASE=2.0,
        FILA_BACKOFF_SINCRONO_TETO=8.0,
        FILA_BACKOFF_ASSINCRONO_BASE=15.0,
        FILA_BACKOFF_ASSINCRONO_TETO=900.0,
    )
    padrao.update(extras)
    return SimpleNamespace(**padrao)


class HandlerDeTeste:
    """Conta quantas execuções coexistem, por classe. Nada de rede."""

    def __init__(self, duracao=DURACAO_CHAMADA):
        self._duracao = duracao
        self._trava = threading.Lock()
        self.em_voo = {}
        self.pico = {}
        self.concluidos = []

    def preparar(self, conn, item):
        return Preparo(payload_enviado={"item": item.id}, chamar=lambda: self._trabalhar(item))

    def _trabalhar(self, item):
        with self._trava:
            atual = self.em_voo.get(item.classe, 0) + 1
            self.em_voo[item.classe] = atual
            self.pico[item.classe] = max(self.pico.get(item.classe, 0), atual)
        try:
            time.sleep(self._duracao)
        finally:
            with self._trava:
                self.em_voo[item.classe] -= 1
                self.concluidos.append(item.id)
        return {"ok": True}

    def interpretar(self, item, bruto):
        return Desfecho.concluido(bruto)

    def verificar(self, conn, item):
        return None


class TestEscalonador(TesteDeFila):
    def _escalonador(self, registry, **extras):
        escalonador = Escalonador(self.engine, self.catalogo, registry,
                                  _configuracao(self.destino, **extras))
        self.addCleanup(escalonador.encerrar)
        return escalonador

    def _drenar(self, escalonador, ids, limite_segundos=25.0):
        """Alimenta os dois despachantes até todos os itens saírem de pendente."""
        fim = time.monotonic() + limite_segundos
        while time.monotonic() < fim:
            escalonador.ciclo_sincrono()
            escalonador.ciclo_assincrono()
            if all(self.status_de(item_id) == "concluido" for item_id in ids):
                return True
            time.sleep(0.05)
        return False

    def test_registry_padrao_so_conhece_os_tipos_ja_entregues(self):
        # Fase 2a: os três de cliente. Fase 3: planilha, sincronização e
        # produto. Fase 5: os três de PCP — `pcp.download_arquivos` só entra
        # quando o worker recebe baixador e pasta de destino, e é por isso que
        # ele não está nesta lista. `vendedor.listar_pagina`, cujo endpoint no
        # ERP ainda não foi confirmado com a Bremen, continua sem handler — e
        # sem handler o motor devolve o item em vez de o perder.
        from app.fila.registry import registry_padrao

        registry = registry_padrao(object())

        self.assertEqual(registry.codigos(), [
            "cliente.atualizar",
            "cliente.consultar",
            "cliente.criar",
            "cliente.planilha_linha",
            "cliente.sincronizar_pagina",
            "pcp.aprovacao.enviar",
            "pcp.orcamento.enviar",
            # Custo da Calculadora de Orçamento: o handler de orçamento, classe síncrona.
            "precificacao.custo.buscar",
            "produto.importar",
        ])
        self.assertIsNone(registry.obter("pcp.download_arquivos"))
        self.assertIsNone(registry.obter("vendedor.listar_pagina"))

    def test_pool_sincrono_processa_um_de_cada_vez_com_o_assincrono_junto(self):
        handler = HandlerDeTeste()
        registry = Registry()
        tipo_sinc = self.criar_tipo(classe="sincrono", timeout_segundos=30)
        tipo_assinc = self.criar_tipo(classe="assincrono", timeout_segundos=30)
        registry.registrar(tipo_sinc, handler)
        registry.registrar(tipo_assinc, handler)

        sincronos = [self.criar_item(tipo_sinc, classe="sincrono") for _ in range(3)]
        assincronos = [self.criar_item(tipo_assinc, classe="assincrono") for _ in range(4)]

        escalonador = self._escalonador(registry)
        self.assertTrue(self._drenar(escalonador, sincronos + assincronos),
                        f"itens não concluíram: {[(i, self.status_de(i)) for i in sincronos + assincronos]}")

        self.assertEqual(handler.pico.get("sincrono"), 1,
                         "dois lançamentos síncronos rodaram ao mesmo tempo")
        self.assertGreaterEqual(handler.pico.get("assincrono", 0), 2,
                                "o pool assíncrono não rodou em paralelo")
        for item_id in sincronos + assincronos:
            self.assertEqual(self.status_de(item_id), "concluido")
            self.assertEqual(self.linha(item_id)["payload_enviado"], {"item": item_id})

    def test_despachante_nao_reivindica_alem_da_capacidade(self):
        handler = HandlerDeTeste(duracao=1.5)
        registry = Registry()
        tipo = self.criar_tipo(classe="sincrono", timeout_segundos=30)
        registry.registrar(tipo, handler)
        ids = [self.criar_item(tipo, classe="sincrono") for _ in range(4)]

        escalonador = self._escalonador(registry)
        self.assertEqual(escalonador.ciclo_sincrono(), 1)
        self.assertEqual(escalonador.ciclo_sincrono(), 0, "reivindicou item sem slot livre")
        self.assertEqual(sum(1 for i in ids if self.status_de(i) == "pendente"), 3)
        self._drenar(escalonador, ids)

    def test_ciclo_completo_grava_a_timeline(self):
        handler = HandlerDeTeste(duracao=0.05)
        registry = Registry()
        tipo = self.criar_tipo(classe="assincrono", timeout_segundos=30)
        registry.registrar(tipo, handler)
        item_id = self.criar_item(tipo, classe="assincrono")

        escalonador = self._escalonador(registry)
        self.assertTrue(self._drenar(escalonador, [item_id]))

        self.assertEqual(self.eventos_de(item_id), ["executando", "concluido"])
        linha = self.linha(item_id)
        self.assertIsNotNone(linha["finalizado_em"])
        self.assertIsNone(linha["lease_expira_em"])
        self.assertIsNotNone(linha["duracao_ms"])
        self.assertIsNone(linha["resultado_aplicado_em"],
                          "o DeskFlow não aplica resultado ao domínio")

    def test_tipo_ativo_sem_handler_volta_a_pendente_sem_queimar_tentativa(self):
        tipo = self.criar_tipo(classe="assincrono")
        item_id = self.criar_item(tipo, classe="assincrono")

        escalonador = self._escalonador(Registry())
        self.assertEqual(escalonador.ciclo_assincrono(), 1)
        fim = time.monotonic() + 10
        while time.monotonic() < fim and self.status_de(item_id) != "pendente":
            time.sleep(0.05)

        self.assertEqual(self.status_de(item_id), "pendente")
        self.assertEqual(self.linha(item_id)["tentativa"], 0)
        self.assertIn("sem_handler", self.eventos_de(item_id))

    def test_heartbeat_do_escalonador(self):
        from sqlalchemy import text

        escalonador = self._escalonador(Registry())
        try:
            escalonador.ciclo_heartbeat()
            with self.engine.connect() as conn:
                linha = conn.execute(text("""
                    SELECT saudavel, capacidade, classes FROM vw_fila_workers WHERE identificador = :id
                """), {"id": escalonador.worker_id}).mappings().one()
            self.assertTrue(linha["saudavel"])
            self.assertEqual(linha["capacidade"], 5)
            self.assertEqual(sorted(linha["classes"]), ["assincrono", "sincrono"])
        finally:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM fila_workers WHERE identificador = :id"),
                             {"id": escalonador.worker_id})


if __name__ == "__main__":
    unittest.main()
