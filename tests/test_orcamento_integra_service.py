import unittest
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.schemas.orcamento import FluxoOrcamentoIntegraRequest, OrcamentoResponse
from app.models.orcamento_processamento import OrcamentoProcessamento
from app.services.orcamento_api_service import OrcamentoAPIService
from app.services.orcamento_integra_service import OrcamentoIntegraService
import app.services.orcamento_integra_service as integra_module


class _ResultadoProdutos:
    def __init__(self, produtos):
        self.produtos = produtos

    def mappings(self):
        return self

    def all(self):
        return self.produtos


class _DbProdutos:
    def __init__(self, produtos):
        self.produtos = produtos

    def execute(self, *_args, **_kwargs):
        return _ResultadoProdutos(self.produtos)

    def rollback(self):
        pass


class _ResultadoScalar:
    def __init__(self, valor):
        self.valor = valor

    def scalar_one(self):
        return self.valor

    def scalar_one_or_none(self):
        return self.valor


class _DbScalar:
    def __init__(self, valor):
        self.valor = valor

    def execute(self, *_args, **_kwargs):
        return _ResultadoScalar(self.valor)

    def rollback(self):
        pass


class _Downloader:
    async def baixar_arquivo(self, _url, destino):
        conteudo = b"arquivo-valido"
        with open(destino, "wb") as arquivo:
            arquivo.write(conteudo)
        return len(conteudo)


class OrcamentoIntegraServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_model_processamento_espelha_tabela_sequelize(self):
        self.assertEqual(
            OrcamentoProcessamento.__tablename__,
            "integra_orcamento_processamentos",
        )
        self.assertEqual(
            set(OrcamentoProcessamento.__table__.columns.keys()),
            {
                "pedido_id",
                "status",
                "id_orcamento",
                "itens_orcamento",
                "resposta_orcamento",
                "resposta_aprovacao",
                "ops",
                "arquivos",
                "erro",
                "criado_em",
                "atualizado_em",
            },
        )
        self.assertTrue(OrcamentoProcessamento.__table__.c.pedido_id.primary_key)

    def test_request_padrao_e_validacao(self):
        self.assertEqual(FluxoOrcamentoIntegraRequest().pedido_ids, [3578])
        with self.assertRaises(ValidationError):
            FluxoOrcamentoIntegraRequest(pedido_ids=[0])
        with self.assertRaises(ValidationError):
            FluxoOrcamentoIntegraRequest(data_entrega="amanhã")

    def test_listagem_classifica_pedido_pronto_e_reconciliacao(self):
        base = {
            "numero_pedido": "LOTE-1",
            "nome_cliente": "Cliente",
            "criado_em": None,
            "status_pedido": "Pedido recebido",
            "id_orcamento": None,
            "ops": None,
            "total_produtos": 1,
            "produtos": ["Agenda"],
            "modelos_pendentes": 0,
            "arquivos_pendentes": 0,
            "total_geral": 2,
            "total_recebidos": 2,
        }
        db = _DbProdutos(
            [
                {**base, "pedido_id": 10, "processamento_status": None},
                {
                    **base,
                    "pedido_id": 11,
                    "processamento_status": "reconciliacao_aprovacao",
                },
            ]
        )

        resposta = OrcamentoIntegraService.listar_pedidos(db, limit=50, offset=0)

        self.assertEqual(resposta.total, 2)
        self.assertTrue(resposta.pedidos[0].elegivel)
        self.assertFalse(resposta.pedidos[1].elegivel)
        self.assertIn("reconciliação", resposta.pedidos[1].motivo_bloqueio)

    def test_calcula_seis_dias_uteis_sem_contar_fim_de_semana(self):
        sexta = datetime(2026, 8, 21, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

        entrega = OrcamentoIntegraService.calcular_data_entrega_seis_dias_uteis(sexta)

        self.assertEqual(entrega, "2026-08-31T12:00:00.000-03:00")

    def test_data_entrega_usa_criado_em_do_pedido(self):
        criado_em = datetime(2026, 8, 21, 9, 0)

        entrega = OrcamentoIntegraService.obter_data_entrega_pedido(
            _DbScalar(criado_em),
            pedido_id=3578,
        )

        self.assertEqual(entrega, "2026-08-31T12:00:00.000-03:00")

    def test_resumo_dashboard_independe_da_tabela_processamento(self):
        resumo = OrcamentoIntegraService.obter_resumo_dashboard(_DbScalar(12))

        self.assertEqual(resumo.recebidos, 12)

    def test_extrai_ops_de_resposta_bremen(self):
        resposta = {"data": [{"id_ops": "109389,109390"}]}
        self.assertEqual(
            OrcamentoIntegraService._extrair_ops(resposta),
            [109389, 109390],
        )

    def test_extensao_segura(self):
        self.assertEqual(
            OrcamentoIntegraService._extensao("https://cdn.exemplo/capa.PNG?x=1", ".pdf"),
            ".png",
        )
        self.assertEqual(
            OrcamentoIntegraService._extensao("https://cdn.exemplo/arquivo", ".pdf"),
            ".pdf",
        )

    async def test_payload_remove_ids_internos_e_preserva_quantidade_paginas(self):
        orcamento = OrcamentoResponse.model_validate(
            {
                "identifier": "PageFlow",
                "data": {
                    "id_cliente": 3366,
                    "id_vendedor": 2284,
                    "id_forma_pagamento": "1",
                    "pedido_ids": [3578],
                    "itens": [
                        {
                            "pedido_id": 3578,
                            "pedido_produto_id": 2467,
                            "id_produto": 10,
                            "titulo": "Agenda",
                            "quantidade": 1,
                            "arquivo_pdf_quantidade_paginas": 218,
                            "componentes": [],
                            "perguntas_gerais": [],
                            "tarefas_gerais": [],
                        }
                    ],
                },
            }
        )
        api = OrcamentoAPIService()
        api._fazer_requisicao_com_retry = AsyncMock(
            return_value={"data": {"id_orcamento": 123}}
        )

        await api.enviar_orcamento(orcamento)

        payload = api._fazer_requisicao_com_retry.await_args.args[1]
        item = payload["data"]["itens"][0]
        self.assertEqual(item["arquivo_pdf_quantidade_paginas"], 218)
        self.assertNotIn("pedido_id", item)
        self.assertNotIn("pedido_produto_id", item)

    async def test_organiza_tres_arquivos_na_op_correspondente(self):
        produtos = [
            {
                "id": 2467,
                "arquivo_pdf": "https://cdn.exemplo/miolo.pdf",
                "design_capa_frente": "https://cdn.exemplo/frente.png",
                "design_capa_verso": "https://cdn.exemplo/verso.png",
            }
        ]
        with tempfile.TemporaryDirectory() as pasta:
            anterior = integra_module.settings.DOWNLOAD_BASE_PATH
            integra_module.settings.DOWNLOAD_BASE_PATH = pasta
            try:
                service = OrcamentoIntegraService(
                    api_service=AsyncMock(),
                    download_service=_Downloader(),
                )
                resultado = await service._organizar_arquivos(
                    _DbProdutos(produtos),
                    pedido_id=3578,
                    ops=[109390],
                )
            finally:
                integra_module.settings.DOWNLOAD_BASE_PATH = anterior

            self.assertEqual(resultado[0]["pedido_produto_id"], 2467)
            self.assertEqual(resultado[0]["id_op"], 109390)
            self.assertEqual(
                sorted(os.listdir(os.path.join(pasta, "109390"))),
                ["arquivo_pdf.pdf", "design_capa_frente.png", "design_capa_verso.png"],
            )


if __name__ == "__main__":
    unittest.main()
