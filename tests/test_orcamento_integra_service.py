import unittest
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.schemas.orcamento import FluxoOrcamentoIntegraRequest, OrcamentoResponse
from app.models.orcamento_processamento import OrcamentoProcessamento
from app.services.orcamento_api_service import OrcamentoAPIService
from app.services.orcamento_integra_service import OrcamentoIntegraService
from app.services.download_bremen_service import DownloadBremenService
import app.services.orcamento_integra_service as integra_module
import app.services.download_bremen_service as download_module


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


class _ResultadoFirst:
    def __init__(self, valor):
        self.valor = valor

    def first(self):
        return self.valor


class _DbCapturaSql:
    def __init__(self, resultado):
        self.resultado = resultado
        self.sql = None
        self.parametros = None

    def execute(self, instrucao, parametros):
        self.sql = str(instrucao)
        self.parametros = parametros
        return _ResultadoFirst(self.resultado)


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


class _DownloaderComFalha:
    async def baixar_arquivo(self, _url, _destino):
        raise RuntimeError("download falhou")


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

    def test_atualiza_status_e_historico_na_mesma_instrucao(self):
        db = _DbCapturaSql(resultado=(901,))

        atualizado = OrcamentoIntegraService._atualizar_status_com_historico(
            db,
            pedido_id=3584,
            id_orcamento=21598,
        )

        self.assertTrue(atualizado)
        self.assertIn("UPDATE integra_pedidos", db.sql)
        self.assertIn("INSERT INTO integra_historico_pedidos", db.sql)
        self.assertIn("status_anterior_id", db.sql)
        self.assertIn("status_novo_id", db.sql)
        self.assertIn("'sistema'", db.sql)
        self.assertEqual(
            db.parametros,
            {"pedido_id": 3584, "id_orcamento": 21598},
        )

    def test_nao_registra_historico_quando_status_nao_foi_trocado(self):
        db = _DbCapturaSql(resultado=None)

        atualizado = OrcamentoIntegraService._atualizar_status_com_historico(
            db,
            pedido_id=3584,
            id_orcamento=21598,
        )

        self.assertFalse(atualizado)

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

    def test_token_blob_nao_e_enviado_para_s3(self):
        token_anterior = download_module.settings.BLOB_READ_WRITE_TOKEN
        download_module.settings.BLOB_READ_WRITE_TOKEN = "token-teste"
        try:
            url_s3, headers_s3 = DownloadBremenService._preparar_download(
                "  https://umapenca.s3.amazonaws.com/products/miolo.pdf  "
            )
            _, headers_vercel = DownloadBremenService._preparar_download(
                "https://arquivos.public.blob.vercel-storage.com/miolo.pdf"
            )
        finally:
            download_module.settings.BLOB_READ_WRITE_TOKEN = token_anterior

        self.assertEqual(
            url_s3,
            "https://umapenca.s3.amazonaws.com/products/miolo.pdf",
        )
        self.assertEqual(headers_s3, {})
        self.assertEqual(headers_vercel, {"Authorization": "Bearer token-teste"})

    def test_rejeita_url_de_download_invalida(self):
        with self.assertRaisesRegex(ValueError, "URL de download inválida"):
            DownloadBremenService._preparar_download("file:///segredo.pdf")

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

    async def test_publica_arquivo_a_arquivo_quando_a_rede_nega_renomear(self):
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
                with patch.object(
                    integra_module.os,
                    "rename",
                    side_effect=PermissionError(13, "Acesso negado"),
                ) as rename, patch.object(
                    integra_module.asyncio, "sleep", AsyncMock()
                ):
                    resultado = await service._organizar_arquivos(
                        _DbProdutos(produtos),
                        pedido_id=4073,
                        ops=[111724],
                    )
            finally:
                integra_module.settings.DOWNLOAD_BASE_PATH = anterior

            pasta_op = os.path.join(pasta, "111724")
            self.assertEqual(rename.call_count, 3)
            self.assertEqual(
                sorted(os.listdir(pasta_op)),
                ["arquivo_pdf.pdf", "design_capa_frente.png", "design_capa_verso.png"],
            )
            self.assertEqual(
                sorted(resultado[0]["arquivos"]),
                [os.path.join(pasta_op, nome) for nome in sorted(os.listdir(pasta_op))],
            )
            self.assertEqual(os.listdir(pasta), ["111724"])

    async def test_nao_deixa_pasta_parcial_quando_a_publicacao_falha(self):
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
                with patch.object(
                    integra_module.os,
                    "rename",
                    side_effect=PermissionError(13, "Acesso negado"),
                ), patch.object(
                    integra_module.shutil,
                    "copy2",
                    side_effect=PermissionError(13, "Acesso negado"),
                ), patch.object(
                    integra_module.asyncio, "sleep", AsyncMock()
                ):
                    with self.assertRaisesRegex(RuntimeError, "Falha ao publicar a pasta"):
                        await service._organizar_arquivos(
                            _DbProdutos(produtos),
                            pedido_id=4073,
                            ops=[111724],
                        )
            finally:
                integra_module.settings.DOWNLOAD_BASE_PATH = anterior

            self.assertEqual(os.listdir(pasta), [])

    async def test_falha_na_limpeza_nao_mascara_erro_do_download(self):
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
            service = OrcamentoIntegraService(
                api_service=AsyncMock(),
                download_service=_DownloaderComFalha(),
            )
            try:
                with patch.object(
                    integra_module.shutil,
                    "rmtree",
                    side_effect=PermissionError("pasta em uso"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "download falhou"):
                        await service._organizar_arquivos(
                            _DbProdutos(produtos),
                            pedido_id=3584,
                            ops=[109404],
                        )
            finally:
                integra_module.settings.DOWNLOAD_BASE_PATH = anterior


if __name__ == "__main__":
    unittest.main()
