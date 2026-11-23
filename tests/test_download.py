import os
import tempfile
import unittest

import httpx

from app.servicos.pcp.download_arquivos import (
    BaixadorArquivos,
    baixar_arquivos_das_ops,
    corrigir_extensao,
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


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF"


class BaixadorDeConteudo:
    """Grava o conteúdo configurado por URL (padrão: PDF)."""

    def __init__(self, conteudos=None):
        self.conteudos = conteudos or {}
        self.urls = []

    def baixar(self, url, destino):
        self.urls.append(url)
        with open(destino, "wb") as arquivo:
            arquivo.write(self.conteudos.get(url, b"%PDF-1.4 teste"))
        return os.path.getsize(destino)


class TestExtensaoPeloConteudo(unittest.TestCase):
    """OP 178630: design de capa em PNG chegava como "<id>_capa_frente.pdf" e
    a produção via o arquivo como PDF corrompido."""

    def _baixar(self, base, nome, conteudo, baixador=None):
        url = "https://parceiro.example/capa"
        baixador = baixador or BaixadorDeConteudo({url: conteudo})
        arquivo = linha(178630, 10, 1, nome, url=url)
        resultado = baixar_arquivos_das_ops(baixador, base, [arquivo])
        return resultado, baixador

    def test_png_com_nome_pdf_e_salvo_como_png(self):
        with tempfile.TemporaryDirectory() as base:
            (total, erros, linhas), _ = self._baixar(base, "4364_capa_frente.pdf", PNG)

            self.assertEqual((total, erros), (1, []))
            pasta = os.path.join(base, "178630")
            self.assertEqual(os.listdir(pasta), ["4364_capa_frente.png"])
            self.assertEqual(linhas[0]["caminho_local"], os.path.join(pasta, "4364_capa_frente.png"))
            self.assertEqual(linhas[0]["tamanho"], len(PNG))

    def test_jpg_com_nome_pdf_e_salvo_como_jpg(self):
        with tempfile.TemporaryDirectory() as base:
            self._baixar(base, "4364_capa_verso.pdf", JPG)
            self.assertEqual(os.listdir(os.path.join(base, "178630")), ["4364_capa_verso.jpg"])

    def test_pdf_e_sinonimos_mantem_o_nome(self):
        with tempfile.TemporaryDirectory() as base:
            self._baixar(base, "4364_miolo.pdf", b"%PDF-1.3")
            self._baixar(base, "foto.jpeg", JPG)
            self.assertEqual(sorted(os.listdir(os.path.join(base, "178630"))), ["4364_miolo.pdf", "foto.jpeg"])

    def test_conteudo_desconhecido_mantem_o_nome(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertLogs("app.servicos.pcp.download_arquivos", level="WARNING"):
                (total, erros, _), _ = self._baixar(base, "capa.pdf", b"<html>erro</html>")
            self.assertEqual((total, erros), (1, []))
            self.assertEqual(os.listdir(os.path.join(base, "178630")), ["capa.pdf"])

    def test_repetir_nao_baixa_de_novo_o_arquivo_ja_corrigido(self):
        with tempfile.TemporaryDirectory() as base:
            self._baixar(base, "4364_capa_frente.pdf", PNG)
            (total, erros, linhas), baixador = self._baixar(base, "4364_capa_frente.pdf", PNG)

            self.assertEqual((total, erros, baixador.urls), (1, [], []))
            self.assertTrue(linhas[0]["caminho_local"].endswith("4364_capa_frente.png"))

    def test_nao_sobrescreve_arquivo_existente_com_a_extensao_corrigida(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "capa.pdf")
            with open(caminho, "wb") as arquivo:
                arquivo.write(PNG)
            with open(os.path.join(pasta, "capa.png"), "wb") as arquivo:
                arquivo.write(b"outro")

            self.assertEqual(corrigir_extensao(caminho), caminho)
            with open(os.path.join(pasta, "capa.png"), "rb") as arquivo:
                self.assertEqual(arquivo.read(), b"outro")


if __name__ == "__main__":
    unittest.main()
