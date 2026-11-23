import os
import tempfile
import unittest

import httpx

from app.servicos.pcp.download_arquivos import (
    BaixadorArquivos,
    baixar_arquivos_das_ops,
    dentro_da_base,
    nomes_unicos,
    sanitizar_nome,
)
from tests.apoio import configuracao


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
            self.assertEqual(os.listdir(pasta), [])
        self.assertEqual(chamadas["n"], 2)


class TestBaixarDireto(unittest.TestCase):
    def test_baixa_direto_na_pasta_da_op_sem_pasta_temporaria(self):
        with tempfile.TemporaryDirectory() as base:
            total, erros, _ = baixar_arquivos_das_ops(BaixadorFalso(), base, [linha(4777, 10, 1, "miolo.pdf")])

            self.assertEqual((total, erros), (1, []))
            # Na base só fica a pasta da OP: nem escola antes, nem pasta temporária.
            self.assertEqual(os.listdir(base), ["4777"])
            self.assertEqual(os.listdir(os.path.join(base, "4777")), ["miolo.pdf"])

    def test_pasta_existente_recebe_os_arquivos_sem_perder_os_antigos(self):
        with tempfile.TemporaryDirectory() as base:
            pasta = os.path.join(base, "4777")
            os.makedirs(pasta)
            with open(os.path.join(pasta, "antigo.pdf"), "wb") as arquivo:
                arquivo.write(b"1")

            baixar_arquivos_das_ops(BaixadorFalso(), base, [linha(4777, 10, 1, "novo.pdf")])

            self.assertEqual(sorted(os.listdir(pasta)), ["antigo.pdf", "novo.pdf"])

    def test_baixador_escreve_part_e_renomeia_no_fim(self):
        baixador = BaixadorArquivos(
            configuracao(),
            http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"%PDF"))),
            dormir=lambda s: None,
        )
        with tempfile.TemporaryDirectory() as pasta:
            destino = os.path.join(pasta, "a.pdf")
            self.assertEqual(baixador.baixar("https://x.public.blob.vercel-storage.com/a.pdf", destino), 4)
            self.assertEqual(os.listdir(pasta), ["a.pdf"])


if __name__ == "__main__":
    unittest.main()
