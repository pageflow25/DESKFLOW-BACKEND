"""Claim: disputa, tipo inativo, trava por chave_bloqueio e teto por tipo."""

import threading
import unittest

from tests.integracao.apoio_banco import TesteDeFila


class TestClaim(TesteDeFila):
    def test_dois_workers_disputando_a_mesma_linha_so_um_ganha(self):
        tipo = self.criar_tipo()
        item_id = self.criar_item(tipo)

        resultados = []
        barreira = threading.Barrier(2)

        def disputar(nome):
            barreira.wait()
            resultados.append([item.id for item in self.reivindicar(worker=nome)])

        threads = [threading.Thread(target=disputar, args=(f"worker-{n}",)) for n in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        vencedores = [ids for ids in resultados if item_id in ids]
        self.assertEqual(len(vencedores), 1, f"a linha foi reivindicada {len(vencedores)}x: {resultados}")
        self.assertEqual(self.status_de(item_id), "reservado")
        linha = self.linha(item_id)
        self.assertEqual(linha["tentativa"], 1)
        self.assertIsNotNone(linha["claim_por"])
        self.assertIsNotNone(linha["lease_expira_em"])

    def test_tipo_inativo_nunca_e_reivindicado(self):
        tipo = self.criar_tipo(ativo=False)
        item_id = self.criar_item(tipo)

        reivindicados = [item.id for item in self.reivindicar()]

        self.assertNotIn(item_id, reivindicados)
        self.assertEqual(self.status_de(item_id), "pendente")

    def test_disponivel_em_no_futuro_nao_e_reivindicado(self):
        tipo = self.criar_tipo()
        item_id = self.criar_item(tipo, disponivel_em_segundos=3600)

        self.assertNotIn(item_id, [item.id for item in self.reivindicar()])
        self.assertEqual(self.status_de(item_id), "pendente")

    def test_tentativas_esgotadas_nao_sao_reivindicadas(self):
        # O claim faz tentativa+1: sem esta guarda, o CHECK
        # ck_fila_processamento_tentativas recusaria a linha.
        tipo = self.criar_tipo(max_tentativas=2)
        item_id = self.criar_item(tipo, max_tentativas=2, tentativa=2)

        self.assertNotIn(item_id, [item.id for item in self.reivindicar()])
        self.assertEqual(self.status_de(item_id), "pendente")

    def test_lease_usa_o_timeout_do_tipo_mais_a_margem(self):
        tipo = self.criar_tipo(timeout_segundos=45)
        item_id = self.criar_item(tipo)

        self.reivindicar(margem=30)

        with self.engine.connect() as conn:
            from sqlalchemy import text
            segundos = conn.execute(text("""
                SELECT EXTRACT(EPOCH FROM (lease_expira_em - claim_em))
                  FROM fila_processamento WHERE id = :id
            """), {"id": item_id}).scalar()
        self.assertAlmostEqual(float(segundos), 75.0, delta=2.0)

    def test_ordem_e_prioridade_desc_depois_disponivel_em_e_id(self):
        tipo = self.criar_tipo()
        baixa = self.criar_item(tipo, prioridade=100)
        alta = self.criar_item(tipo, prioridade=900)
        media = self.criar_item(tipo, prioridade=500)

        ids = [item.id for item in self.reivindicar(lote=3)]

        self.assertEqual(ids[:3], [alta, media, baixa])


class TestChaveBloqueio(TesteDeFila):
    def test_dois_itens_com_a_mesma_chave_nao_saem_no_mesmo_lote(self):
        tipo = self.criar_tipo()
        primeiro = self.criar_item(tipo, chave_bloqueio="cliente:4242", prioridade=900)
        segundo = self.criar_item(tipo, chave_bloqueio="cliente:4242", prioridade=800)

        ids = [item.id for item in self.reivindicar(lote=10)]

        self.assertIn(primeiro, ids)
        self.assertNotIn(segundo, ids)
        self.assertEqual(self.status_de(segundo), "pendente")

    def test_segunda_rodada_continua_travada_enquanto_a_primeira_ocupa_worker(self):
        tipo = self.criar_tipo()
        primeiro = self.criar_item(tipo, chave_bloqueio="cliente:4243", prioridade=900)
        segundo = self.criar_item(tipo, chave_bloqueio="cliente:4243", prioridade=800)

        self.assertEqual([item.id for item in self.reivindicar()], [primeiro])
        # O primeiro segue em `reservado` (ocupa_worker): o segundo não pode
        # sair nem numa transação de claim nova.
        self.assertEqual([item.id for item in self.reivindicar(worker="worker-b")], [])
        self.assertEqual(self.status_de(segundo), "pendente")

    def test_chave_diferente_sai_junto(self):
        tipo = self.criar_tipo()
        um = self.criar_item(tipo, chave_bloqueio="cliente:1")
        outro = self.criar_item(tipo, chave_bloqueio="cliente:2")

        ids = [item.id for item in self.reivindicar(lote=10)]

        self.assertCountEqual(ids, [um, outro])

    def test_classes_diferentes_com_a_mesma_chave_nao_rodam_em_paralelo(self):
        # A planilha (assíncrona) e o cadastro pela tela (síncrono) podem tocar
        # o mesmo cliente. Pools separados não podem furar a trava.
        tipo_sinc = self.criar_tipo(classe="sincrono")
        tipo_assinc = self.criar_tipo(classe="assincrono")
        sincrono = self.criar_item(tipo_sinc, classe="sincrono", chave_bloqueio="cliente:9")
        assincrono = self.criar_item(tipo_assinc, classe="assincrono", chave_bloqueio="cliente:9")

        self.assertEqual([item.id for item in self.reivindicar(classe="sincrono")], [sincrono])
        self.assertEqual([item.id for item in self.reivindicar(classe="assincrono")], [])
        self.assertEqual(self.status_de(assincrono), "pendente")

    def test_claim_concorrente_na_mesma_chave_so_um_ganha(self):
        tipo = self.criar_tipo(classe="sincrono")
        tipo_b = self.criar_tipo(classe="assincrono")
        um = self.criar_item(tipo, classe="sincrono", chave_bloqueio="cliente:77")
        dois = self.criar_item(tipo_b, classe="assincrono", chave_bloqueio="cliente:77")

        obtidos = []
        barreira = threading.Barrier(2)

        def disputar(classe, worker):
            barreira.wait()
            obtidos.extend(item.id for item in self.reivindicar(classe=classe, worker=worker))

        threads = [
            threading.Thread(target=disputar, args=("sincrono", "worker-s")),
            threading.Thread(target=disputar, args=("assincrono", "worker-a")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(len(obtidos), 1, f"os dois lados da mesma chave saíram juntos: {obtidos}")
        self.assertIn(obtidos[0], (um, dois))


class TestTetoPorTipo(TesteDeFila):
    def test_teto_do_tipo_limita_o_lote(self):
        tipo = self.criar_tipo(concorrencia_maxima=2)
        ids = [self.criar_item(tipo) for _ in range(5)]

        reivindicados = [item.id for item in self.reivindicar(lote=10)]

        self.assertEqual(len(reivindicados), 2)
        for item_id in set(ids) - set(reivindicados):
            self.assertEqual(self.status_de(item_id), "pendente")

    def test_teto_conta_o_que_ja_ocupa_worker(self):
        tipo = self.criar_tipo(concorrencia_maxima=2)
        self.criar_item(tipo, status="executando")
        restantes = [self.criar_item(tipo) for _ in range(3)]

        reivindicados = [item.id for item in self.reivindicar(lote=10)]

        self.assertEqual(len(reivindicados), 1)
        self.assertEqual(sum(1 for i in restantes if self.status_de(i) == "pendente"), 2)

    def test_teto_1_preserva_a_garantia_sequencial_da_planilha(self):
        tipo = self.criar_tipo(concorrencia_maxima=1)
        ids = [self.criar_item(tipo) for _ in range(3)]

        self.assertEqual(len(self.reivindicar(lote=10)), 1)
        self.assertEqual(len(self.reivindicar(worker="worker-b", lote=10)), 0)
        self.assertEqual(sum(1 for i in ids if self.status_de(i) == "pendente"), 2)

    def test_sem_teto_o_lote_inteiro_sai(self):
        tipo = self.criar_tipo(concorrencia_maxima=None)
        [self.criar_item(tipo) for _ in range(4)]

        self.assertEqual(len(self.reivindicar(lote=10)), 4)


if __name__ == "__main__":
    unittest.main()
