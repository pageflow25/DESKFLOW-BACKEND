"""Reaper, backoff e heartbeat."""

import unittest
from types import SimpleNamespace

from sqlalchemy import text

from deskflow2.fila import repositorio
from deskflow2.fila.motor import Motor, backoff_com_jitter
from deskflow2.fila.modelos import Desfecho
from deskflow2.fila.registry import Registry
from tests.integracao.apoio_banco import TesteDeFila

ATOR = "deskflow:teste"


def _configuracao(**extras):
    padrao = dict(
        FILA_LEASE_MARGEM_SEGUNDOS=30,
        FILA_BACKOFF_SINCRONO_BASE=2.0,
        FILA_BACKOFF_SINCRONO_TETO=8.0,
        FILA_BACKOFF_ASSINCRONO_BASE=15.0,
        FILA_BACKOFF_ASSINCRONO_TETO=900.0,
    )
    padrao.update(extras)
    return SimpleNamespace(**padrao)


class TestReaper(TesteDeFila):
    def _reaper(self, minutos=15, passo=10, teto=690):
        with self.engine.begin() as conn:
            return repositorio.reaper(conn, self.catalogo, ATOR, minutos, passo, teto)

    def test_lease_expirado_em_reservado_volta_a_pendente(self):
        # A chamada não chegou a sair: devolver é seguro mesmo sem idempotência.
        tipo = self.criar_tipo(idempotente=False)
        item_id = self.criar_item(tipo, status="reservado", lease_segundos=-60, tentativa=1)

        self._reaper()

        self.assertEqual(self.status_de(item_id), "pendente")
        linha = self.linha(item_id)
        self.assertIsNone(linha["claim_por"])
        self.assertIsNone(linha["lease_expira_em"])
        self.assertEqual(linha["erro_codigo"], "LEASE_EXPIRADO")
        self.assertIn("lease_expirado_devolvido", self.eventos_de(item_id))

    def test_lease_expirado_em_executando_nao_idempotente_vira_incerto(self):
        # Regra crítica: o destino PODE ter processado. Nunca volta a pendente.
        tipo = self.criar_tipo(idempotente=False)
        item_id = self.criar_item(tipo, status="executando", lease_segundos=-60, tentativa=1)

        resumo = self._reaper()

        self.assertEqual(self.status_de(item_id), "incerto")
        self.assertGreaterEqual(resumo["incertos"], 1)
        self.assertEqual(self.linha(item_id)["erro_codigo"], "LEASE_EXPIRADO_EM_VOO")
        self.assertIn("lease_expirado_incerto", self.eventos_de(item_id))

    def test_lease_expirado_em_executando_idempotente_volta_a_pendente(self):
        tipo = self.criar_tipo(idempotente=True)
        item_id = self.criar_item(tipo, status="executando", lease_segundos=-60, tentativa=1)

        self._reaper()

        self.assertEqual(self.status_de(item_id), "pendente")

    def test_lease_valido_nao_e_colhido(self):
        tipo = self.criar_tipo()
        item_id = self.criar_item(tipo, status="executando", lease_segundos=600, tentativa=1)

        self._reaper()

        self.assertEqual(self.status_de(item_id), "executando")

    def test_lease_expirado_sem_tentativa_sobrando_vai_para_dlq(self):
        # Sem isto a linha voltaria a `pendente` e nunca mais seria
        # reivindicada (o claim exige tentativa < max_tentativas).
        tipo = self.criar_tipo(max_tentativas=2)
        item_id = self.criar_item(tipo, status="reservado", lease_segundos=-60,
                                  tentativa=2, max_tentativas=2)

        self._reaper()

        self.assertEqual(self.status_de(item_id), "dlq")
        linha = self.linha(item_id)
        self.assertIsNotNone(linha["dlq_em"])
        self.assertIsNotNone(linha["finalizado_em"])

    def test_envelhecimento_sobe_a_prioridade_com_teto(self):
        tipo = self.criar_tipo(classe="assincrono")
        novo = self.criar_item(tipo, classe="assincrono", prioridade=200)
        no_teto = self.criar_item(tipo, classe="assincrono", prioridade=690)
        with self.engine.begin() as conn:
            conn.execute(text("""
                UPDATE fila_processamento SET atualizado_em = now() - interval '30 minutes'
                 WHERE id = ANY(:ids)
            """), {"ids": [novo, no_teto]})

        self._reaper(minutos=15, passo=10, teto=690)

        self.assertEqual(self.linha(novo)["prioridade"], 210)
        self.assertEqual(self.linha(no_teto)["prioridade"], 690)
        self.assertIn("envelhecido", self.eventos_de(novo))

    def test_envelhecimento_nao_repete_antes_do_intervalo(self):
        tipo = self.criar_tipo(classe="assincrono")
        item_id = self.criar_item(tipo, classe="assincrono", prioridade=200)
        with self.engine.begin() as conn:
            conn.execute(text("""
                UPDATE fila_processamento SET atualizado_em = now() - interval '30 minutes'
                 WHERE id = :id
            """), {"id": item_id})

        self._reaper()
        self._reaper()

        self.assertEqual(self.linha(item_id)["prioridade"], 210)

    def test_sincrono_nao_envelhece(self):
        tipo = self.criar_tipo(classe="sincrono")
        item_id = self.criar_item(tipo, classe="sincrono", prioridade=900)
        with self.engine.begin() as conn:
            conn.execute(text("""
                UPDATE fila_processamento SET atualizado_em = now() - interval '30 minutes'
                 WHERE id = :id
            """), {"id": item_id})

        self._reaper()

        self.assertEqual(self.linha(item_id)["prioridade"], 900)


class TestBackoff(TesteDeFila):
    def test_jitter_completo_sorteia_entre_zero_e_o_teto_exponencial(self):
        # uniform(0, min(base * 2^(n-1), teto)), não ±10 %.
        self.assertEqual(backoff_com_jitter(1, 2.0, 8.0, sorteio=lambda a, b: b), 2.0)
        self.assertEqual(backoff_com_jitter(2, 2.0, 8.0, sorteio=lambda a, b: b), 4.0)
        self.assertEqual(backoff_com_jitter(3, 2.0, 8.0, sorteio=lambda a, b: b), 8.0)
        self.assertEqual(backoff_com_jitter(9, 2.0, 8.0, sorteio=lambda a, b: b), 8.0)
        self.assertEqual(backoff_com_jitter(3, 2.0, 8.0, sorteio=lambda a, b: a), 0.0)

    def test_retentativa_grava_disponivel_em_no_futuro_sem_dormir(self):
        import time

        tipo = self.criar_tipo(classe="assincrono", max_tentativas=3)
        item_id = self.criar_item(tipo, classe="assincrono", max_tentativas=3)
        item = self.reivindicar()[0]
        motor = Motor(self.engine, self.catalogo, Registry(),
                      _configuracao(FILA_BACKOFF_ASSINCRONO_BASE=120.0,
                                    FILA_BACKOFF_ASSINCRONO_TETO=120.0), "worker-teste")

        inicio = time.monotonic()
        estado = motor._aplicar(item, Desfecho.retentar("503 do destino", "DESTINO_INDISPONIVEL"),
                                de_status=self.catalogo.id_status("reservado"), duracao_ms=12)
        decorrido = time.monotonic() - inicio

        self.assertEqual(estado, "pendente")
        self.assertLess(decorrido, 5.0, "a thread dormiu esperando o destino")
        self.assertEqual(self.status_de(item_id), "pendente")
        with self.engine.connect() as conn:
            adiante = conn.execute(text("""
                SELECT EXTRACT(EPOCH FROM (disponivel_em - now())) FROM fila_processamento WHERE id = :id
            """), {"id": item_id}).scalar()
        self.assertGreater(float(adiante), 0.0)
        self.assertIn("retentativa_agendada", self.eventos_de(item_id))
        # O item volta a pendente, mas só sai de novo quando `disponivel_em` chegar.
        self.assertEqual(self.reivindicar(), [])

    def test_ultima_tentativa_retentavel_vai_para_dlq(self):
        tipo = self.criar_tipo(max_tentativas=1)
        item_id = self.criar_item(tipo, max_tentativas=1)
        item = self.reivindicar()[0]
        motor = Motor(self.engine, self.catalogo, Registry(), _configuracao(), "worker-teste")

        estado = motor._aplicar(item, Desfecho.retentar("503", "DESTINO_INDISPONIVEL"),
                                de_status=self.catalogo.id_status("reservado"), duracao_ms=None)

        self.assertEqual(estado, "dlq")
        self.assertEqual(self.status_de(item_id), "dlq")

    def test_falha_definitiva_encerra_na_hora(self):
        # Falha definitiva não gasta o orçamento de tentativas em espera: vai
        # direto a terminal em vez de repetir 3x o mesmo 400.
        tipo = self.criar_tipo(max_tentativas=5)
        item_id = self.criar_item(tipo, max_tentativas=5)
        item = self.reivindicar()[0]
        motor = Motor(self.engine, self.catalogo, Registry(), _configuracao(), "worker-teste")

        estado = motor._aplicar(item, Desfecho.falhou("CNPJ inválido", "HTTP_400"),
                                de_status=self.catalogo.id_status("reservado"), duracao_ms=30)

        self.assertEqual(estado, "falhou")
        linha = self.linha(item_id)
        self.assertEqual(linha["tentativa"], 1)
        self.assertIsNotNone(linha["finalizado_em"])
        self.assertEqual(linha["erro_codigo"], "HTTP_400")


class TestHeartbeat(TesteDeFila):
    def test_heartbeat_aparece_em_vw_fila_workers(self):
        import uuid

        identificador = f"teste-{uuid.uuid4().hex[:10]}"
        try:
            with self.engine.begin() as conn:
                repositorio.heartbeat(conn, identificador, ["sincrono", "assincrono"], "2.0.0",
                                      1234, 5, 0, {"destino": self.destino})
            with self.engine.connect() as conn:
                linha = conn.execute(text("""
                    SELECT saudavel, segundos_sem_heartbeat, capacidade, itens_com_claim
                      FROM vw_fila_workers WHERE identificador = :id
                """), {"id": identificador}).mappings().one()
            self.assertTrue(linha["saudavel"])
            self.assertLess(linha["segundos_sem_heartbeat"], 60)
            self.assertEqual(linha["capacidade"], 5)
            self.assertEqual(linha["itens_com_claim"], 0)
        finally:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM fila_workers WHERE identificador = :id"),
                             {"id": identificador})


if __name__ == "__main__":
    unittest.main()
