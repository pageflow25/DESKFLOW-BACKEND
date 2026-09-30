import unittest
from types import SimpleNamespace

from deskflow2.jobs import _protegido, criar_agendador
from tests.apoio import configuracao


class TestJobs(unittest.TestCase):
    """Agendamento dos ciclos do worker.

    Antes da Fase 6b este arquivo afirmava os quatro ciclos ANTIGOS do PCP
    (orcamentos, aprovacoes, downloads, reconciliacao), que faziam claim direto
    nas tabelas `orcamento_api_*`. Eles sairam junto com
    `intervalo_reconciliacao`; o mesmo trabalho e feito pelos handlers `pcp.*`
    da fila. O que vale afirmar continua sendo o mesmo, sobre os ciclos de hoje:
    quais sao, com que intervalo, e que um ciclo com erro nao derruba o worker.
    """

    def _app(self, **overrides):
        def ciclo():
            return 0

        fila = SimpleNamespace(
            ciclo_sincrono=ciclo, ciclo_assincrono=ciclo,
            ciclo_reaper=ciclo, ciclo_heartbeat=ciclo,
        )
        return SimpleNamespace(settings=configuracao(**overrides), fila=fila)

    def test_agenda_os_quatro_ciclos_da_fila_sem_sobreposicao(self):
        agendador = criar_agendador(self._app(
            FILA_POLL_SINCRONO_SEGUNDOS=1,
            FILA_POLL_ASSINCRONO_SEGUNDOS=5,
        ))

        jobs = {job.id: job for job in agendador.get_jobs()}
        self.assertEqual(
            set(jobs),
            {"fila_sincrona", "fila_assincrona", "fila_reaper", "fila_heartbeat"},
        )
        # O poll curto do sincrono e o que faz a latencia caber nos 30 s
        # aceitaveis sem LISTEN/NOTIFY, indisponivel no pooler de transacao.
        self.assertEqual(jobs["fila_sincrona"].trigger.interval.total_seconds(), 1)
        self.assertEqual(jobs["fila_assincrona"].trigger.interval.total_seconds(), 5)

        # Os padroes so sao copiados para os jobs quando o agendador sobe.
        self.assertEqual(agendador._job_defaults["max_instances"], 1)
        self.assertTrue(agendador._job_defaults["coalesce"])

    def test_intervalo_minimo_e_respeitado_mesmo_com_config_zerada(self):
        agendador = criar_agendador(self._app(
            FILA_POLL_SINCRONO_SEGUNDOS=0,
            FILA_POLL_ASSINCRONO_SEGUNDOS=0,
            FILA_REAPER_SEGUNDOS=0,
            FILA_HEARTBEAT_SEGUNDOS=0,
        ))

        jobs = {job.id: job for job in agendador.get_jobs()}
        # Sem piso, um 0 na config viraria busy-loop de poll contra o banco.
        self.assertEqual(jobs["fila_sincrona"].trigger.interval.total_seconds(), 1)
        self.assertEqual(jobs["fila_assincrona"].trigger.interval.total_seconds(), 1)
        self.assertEqual(jobs["fila_reaper"].trigger.interval.total_seconds(), 5)
        self.assertEqual(jobs["fila_heartbeat"].trigger.interval.total_seconds(), 5)

    def test_sem_escalonador_o_agendador_sobe_vazio(self):
        # FILA_ATIVA=false, ou catalogo ausente no banco: o worker sobe e nao
        # reivindica nada, em vez de estourar no boot.
        agendador = criar_agendador(SimpleNamespace(settings=configuracao(), fila=None))

        self.assertEqual(agendador.get_jobs(), [])

    def test_ciclo_com_erro_nao_derruba_o_worker(self):
        def quebra():
            raise RuntimeError("banco fora")

        with self.assertLogs("deskflow2.jobs", level="ERROR"):
            _protegido("fila_sincrona", quebra)()


if __name__ == "__main__":
    unittest.main()
