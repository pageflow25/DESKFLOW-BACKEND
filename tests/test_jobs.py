import unittest
from types import SimpleNamespace

from deskflow2.jobs import _protegido, criar_agendador, intervalo_reconciliacao
from tests.apoio import configuracao


class TestJobs(unittest.TestCase):
    def test_agenda_os_quatro_ciclos_sem_sobreposicao(self):
        servico = SimpleNamespace(executar_ciclo=lambda: 0)
        app = SimpleNamespace(
            settings=configuracao(PCP_ENVIO_INTERVALO_SEGUNDOS=60),
            orcamentos=servico, aprovacoes=servico, downloads=servico, reconciliacao=servico,
        )

        agendador = criar_agendador(app)

        jobs = {job.id: job for job in agendador.get_jobs()}
        self.assertEqual(set(jobs), {"orcamentos", "aprovacoes", "downloads", "reconciliacao"})
        self.assertEqual(jobs["orcamentos"].trigger.interval.total_seconds(), 60)
        self.assertEqual(jobs["reconciliacao"].trigger.interval.total_seconds(), 300)
        # Os padrões só são copiados para os jobs quando o agendador sobe.
        self.assertEqual(agendador._job_defaults["max_instances"], 1)
        self.assertTrue(agendador._job_defaults["coalesce"])

    def test_ciclo_com_erro_nao_derruba_o_worker(self):
        def quebra():
            raise RuntimeError("banco fora")

        with self.assertLogs("deskflow2.jobs", level="ERROR"):
            _protegido("orcamentos", quebra)()


if __name__ == "__main__":
    unittest.main()


class IntervaloReconciliacaoTest(unittest.TestCase):
    def test_segue_a_config_entre_1_e_5_minutos(self):
        self.assertEqual(intervalo_reconciliacao(1), 60)
        self.assertEqual(intervalo_reconciliacao(3), 180)
        self.assertEqual(intervalo_reconciliacao(30), 300)
        self.assertEqual(intervalo_reconciliacao(0), 60)
