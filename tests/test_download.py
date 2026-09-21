import os
import tempfile
import unittest
from unittest import mock

import httpx

from deskflow2.servicos import download_arquivos
from deskflow2.servicos.download_arquivos import (
    BaixadorArquivos,
    DownloadArquivos,
    dentro_da_base,
    nomes_unicos,
    publicar_arquivos,
    sanitizar_nome,
)
from tests.apoio import MotorFalso, RepassadorFalso, configuracao


class BaixadorFalso:
    def __init__(self, falhar=()):
        self.falhar = set(falhar)
        self.urls = []

    def baixar(self, url, destino):
        self.urls.append(url)
        if url in self.falhar:
            raise RuntimeError("falha ao baixar (HTTP 404)")
        with open(destino, "wb") as arquivo:
            arquivo.write(b"%PDF-1.4 teste")
        return os.path.getsize(destino)


def linha(id_op, pedido, arquivo_id, nome, url=None):
    return {
        "id_op": id_op, "pedido_distribuicao_id": pedido, "arquivo_pdf_id": arquivo_id,
        "arquivo_nome": nome, "url": url or f"https://x.public.blob.vercel-storage.com/{arquivo_id}.pdf",
        "tipo_arquivo": "application/pdf", "escola_nome": "Colégio: Exemplo/Centro",
    }


class TestNomes(unittest.TestCase):
    def test_sanitiza_nomes_para_windows(self):
        # Separadores viram "_": o nome nunca sobe de pasta.
        self.assertEqual(sanitizar_nome('..\\..\\capa:final?.pdf', "x"), ".._.._capa_final_.pdf")
        self.assertEqual(sanitizar_nome("..", "padrao"), "padrao")
        self.assertEqual(sanitizar_nome("  ", "padrao"), "padrao")
        self.assertEqual(sanitizar_nome("Escola. ", "x"), "Escola")

    def test_nomes_repetidos_ganham_o_id_do_arquivo(self):
        arquivos = [{"arquivo_pdf_id": 1, "arquivo_nome": "capa.pdf"}, {"arquivo_pdf_id": 2, "arquivo_nome": "CAPA.pdf"}]
        self.assertEqual(nomes_unicos(arquivos), ["capa.pdf", "CAPA_2.pdf"])

    def test_caminho_fora_da_base(self):
        base = os.path.abspath("base")
        self.assertTrue(dentro_da_base(base, os.path.join(base, "escola", "123")))
        self.assertFalse(dentro_da_base(base, os.path.join(base, "..", "fora")))


class TestBaixador(unittest.TestCase):
    def test_token_do_blob_so_vai_para_o_blob(self):
        baixador = BaixadorArquivos(configuracao(), http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
        self.assertIn("Authorization", baixador._headers("https://abc.public.blob.vercel-storage.com/a.pdf"))
        self.assertEqual(baixador._headers("https://s3.amazonaws.com/a.pdf"), {})
        with self.assertRaises(ValueError):
            baixador._headers("file:///c:/segredo.pdf")

    def test_arquivo_vazio_e_repetido_e_falha(self):
        chamadas = {"n": 0}

        def roteador(request):
            chamadas["n"] += 1
            return httpx.Response(200, content=b"")

        baixador = BaixadorArquivos(
            configuracao(DOWNLOAD_TENTATIVAS=2),
            http=httpx.Client(transport=httpx.MockTransport(roteador)),
            dormir=lambda s: None,
        )
        with tempfile.TemporaryDirectory() as pasta:
            destino = os.path.join(pasta, "a.pdf")
            with self.assertRaises(RuntimeError):
                baixador.baixar("https://x.public.blob.vercel-storage.com/a.pdf", destino)
            self.assertFalse(os.path.exists(destino))
        self.assertEqual(chamadas["n"], 2)


class TestPublicar(unittest.TestCase):
    def test_pasta_existente_recebe_os_arquivos_sem_perder_os_antigos(self):
        with tempfile.TemporaryDirectory() as raiz:
            stage, final = os.path.join(raiz, "stage"), os.path.join(raiz, "final")
            os.makedirs(stage)
            os.makedirs(final)
            with open(os.path.join(final, "antigo.pdf"), "wb") as arquivo:
                arquivo.write(b"1")
            with open(os.path.join(stage, "novo.pdf"), "wb") as arquivo:
                arquivo.write(b"2")

            publicar_arquivos(stage, final, dormir=lambda s: None)

            self.assertEqual(sorted(os.listdir(final)), ["antigo.pdf", "novo.pdf"])


class TestDownloadArquivos(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.fila = mock.patch.object(download_arquivos, "fila").start()
        self.addCleanup(mock.patch.stopall)
        self.repassador = RepassadorFalso()

    def servico(self, baixador):
        config = configuracao(DOWNLOAD_BASE_PATH=self.pasta.name)
        return DownloadArquivos(MotorFalso(), baixador, self.repassador, config, dormir=lambda s: None)

    def test_baixa_por_op_na_pasta_da_escola_e_grava_downloads_bremen(self):
        self.fila.arquivos_da_aprovacao.return_value = [
            linha(106403, 11, 1, "miolo.pdf"),
            linha(106403, 12, 1, "miolo.pdf"),  # mesmo arquivo, dois pedidos agrupados
            linha(106403, 11, 2, "capa.pdf"),
            linha(106404, 13, 3, "miolo.pdf"),
        ]
        baixador = BaixadorFalso()

        resultado = self.servico(baixador).baixar_aprovacao(900)

        self.assertEqual(resultado, {"sucesso": True, "total_arquivos": 3, "erros": []})
        self.assertEqual(len(baixador.urls), 3)
        pasta_op = os.path.join(self.pasta.name, "Colégio_ Exemplo_Centro", "106403")
        self.assertEqual(sorted(os.listdir(pasta_op)), ["capa.pdf", "miolo.pdf"])
        linhas = self.fila.registrar_downloads_bremen.call_args.args[1]
        self.assertEqual(len(linhas), 4)
        self.assertEqual({l["distribuicao_material_id"] for l in linhas if l["arquivo_pdf_id"] == 1}, {11, 12})
        self.assertEqual(self.repassador.enviados[-1], ("downloads", 900, resultado))
        self.assertFalse([n for n in os.listdir(self.pasta.name) if n.startswith(".deskflow2-")])

    def test_falha_parcial_reporta_erro_e_repeticao_baixa_so_o_que_falta(self):
        url_capa = "https://x.public.blob.vercel-storage.com/capa.pdf"
        self.fila.arquivos_da_aprovacao.return_value = [
            linha(106403, 11, 1, "miolo.pdf"),
            linha(106403, 11, 2, "capa.pdf", url=url_capa),
        ]

        primeiro = self.servico(BaixadorFalso(falhar=[url_capa])).baixar_aprovacao(900)
        self.assertFalse(primeiro["sucesso"])
        self.assertEqual(primeiro["total_arquivos"], 1)
        self.assertIn("capa.pdf", primeiro["erros"][0])

        baixador = BaixadorFalso()
        segundo = self.servico(baixador).baixar_aprovacao(900)
        self.assertTrue(segundo["sucesso"])
        self.assertEqual(baixador.urls, [url_capa])

    def test_aprovacao_sem_op_vinculada_reporta_erro(self):
        self.fila.arquivos_da_aprovacao.return_value = []

        resultado = self.servico(BaixadorFalso()).baixar_aprovacao(900)

        self.assertFalse(resultado["sucesso"])
        self.assertEqual(self.repassador.enviados[-1][0], "downloads")


if __name__ == "__main__":
    unittest.main()
