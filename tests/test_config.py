import os
import unittest
from unittest import mock

from pydantic import ValidationError

from app.core.config import Settings

OBRIGATORIAS = dict(
    DATABASE_URL="postgresql://u:p@localhost:5432/teste",
    ERP_BASE_URL="https://erp.teste/",
    ERP_USER="u",
    ERP_PASSWORD="p",
    FILA_POOL_SINCRONO="3",
    FILA_POOL_ASSINCRONO="4",
    ERP_MAX_CONEXOES="8",
    ERP_CONEXOES_RESERVADAS_SINCRONO="4",
)


def _settings(**variaveis):
    """Settings lido SÓ das variáveis passadas: sem o .env da pasta e sem o
    ambiente da máquina, para o teste não depender de onde roda."""
    with mock.patch.dict(os.environ, variaveis, clear=True):
        return Settings(_env_file=None)


class TestTamanhoDosPoolsVemDoEnv(unittest.TestCase):
    """Threads e conexões ao ERP não têm padrão no código: vêm 100 % do .env,
    e valor ausente ou fora da faixa recusa o boot em vez de ser corrigido."""

    def test_le_os_valores_do_ambiente(self):
        settings = _settings(**OBRIGATORIAS)
        self.assertEqual(settings.FILA_POOL_SINCRONO, 3)
        self.assertEqual(settings.FILA_POOL_ASSINCRONO, 4)
        self.assertEqual(settings.ERP_MAX_CONEXOES, 8)
        self.assertEqual(settings.ERP_CONEXOES_RESERVADAS_SINCRONO, 4)

    def test_ausente_recusa_o_boot(self):
        for chave in ("FILA_POOL_SINCRONO", "FILA_POOL_ASSINCRONO",
                      "ERP_MAX_CONEXOES", "ERP_CONEXOES_RESERVADAS_SINCRONO"):
            with self.subTest(chave=chave):
                variaveis = {k: v for k, v in OBRIGATORIAS.items() if k != chave}
                with self.assertRaises(ValidationError) as ctx:
                    _settings(**variaveis)
                self.assertIn(chave, str(ctx.exception))

    def test_pool_ou_teto_menor_que_um_recusa_o_boot(self):
        for chave in ("FILA_POOL_SINCRONO", "FILA_POOL_ASSINCRONO", "ERP_MAX_CONEXOES"):
            with self.subTest(chave=chave):
                with self.assertRaises(ValidationError) as ctx:
                    _settings(**{**OBRIGATORIAS, chave: "0"})
                self.assertIn(chave, str(ctx.exception))

    def test_reserva_tem_de_caber_no_teto(self):
        for reserva in ("8", "9", "-1"):
            with self.subTest(reserva=reserva):
                with self.assertRaises(ValidationError) as ctx:
                    _settings(**{**OBRIGATORIAS, "ERP_CONEXOES_RESERVADAS_SINCRONO": reserva})
                self.assertIn("ERP_CONEXOES_RESERVADAS_SINCRONO", str(ctx.exception))

    def test_reserva_zero_e_aceita(self):
        settings = _settings(**{**OBRIGATORIAS, "ERP_CONEXOES_RESERVADAS_SINCRONO": "0"})
        self.assertEqual(settings.ERP_CONEXOES_RESERVADAS_SINCRONO, 0)


if __name__ == "__main__":
    unittest.main()
