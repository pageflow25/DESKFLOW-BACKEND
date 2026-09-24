"""Consumo de lotes de INTEGRAÇÃO (orcamento_api_lotes.origem = 'integracao').

Cobre o que muda no DeskFlow quando o orçamento não vem de pedidos de escola:
a escolha do SQL, o parâmetro de ids, a conferência do `codigo_externo` contra
os produtos do parceiro, e o download dos arquivos (URLs no próprio produto,
pasta por integração/pedido, linha de downloads_bremen no lado certo do arco).
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from deskflow2.repositorios.fila import (
    ORIGEM_ESCOLA,
    ORIGEM_INTEGRACAO,
    OrcamentoReivindicado,
)
from deskflow2.servicos import payload as modulo_payload
from deskflow2.servicos.download_arquivos import DownloadArquivos, chave_arquivo
from deskflow2.servicos.payload import (
    ARQUIVO_INTEGRACAO,
    PARAMETRO_IDS_ESCOLA,
    PARAMETRO_IDS_INTEGRACAO,
    PayloadIncompleto,
    _arquivo_e_parametro,
    montar_payload_orcamento,
)
from tests.apoio import MotorFalso, RepassadorFalso, configuracao


def orcamento_integracao(**campos):
    base = dict(
        id=700, requisicao_id=300, lote_id=5, modo_envio="assincrono", url_webhook="https://pf/x",
        modo_agrupamento=None, cliente_id=3407, vendedor_id=2153, forma_pagamento=1,
        pedido_distribuicao_ids=[], origem=ORIGEM_INTEGRACAO,
        integra_pedido_produto_ids=[9001, 9002],
        data_entrega="01/12/2026",
    )
    base.update(campos)
    return OrcamentoReivindicado(**base)


def orcamento_escola(**campos):
    base = dict(
        id=700, requisicao_id=300, lote_id=5, modo_envio="assincrono", url_webhook="https://pf/x",
        modo_agrupamento="unidade", cliente_id=501, vendedor_id=7, forma_pagamento=11,
        pedido_distribuicao_ids=[1, 2],
    )
    base.update(campos)
    return OrcamentoReivindicado(**base)


class ConexaoFalsa:
    def __init__(self, linhas):
        self.linhas = linhas
        self.parametros = None
        self.consulta = None

    def execute(self, consulta, parametros):
        self.consulta = consulta
        self.parametros = parametros
        linhas = self.linhas
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: linhas))


def corpo_sql(*codigos):
    return {"data": {
        "id_cliente": 3407, "id_vendedor": 2153, "id_forma_pagamento": "1",
        "itens": [{"id_produto": 311, "codigo_externo": c, "quantidade": 1} for c in codigos],
    }}


class TestOrcamentoReivindicado(unittest.TestCase):
    def test_ids_origem_segue_a_origem_do_lote(self):
        self.assertEqual(orcamento_integracao().ids_origem, [9001, 9002])
        self.assertEqual(orcamento_escola().ids_origem, [1, 2])

    def test_origem_padrao_e_escola(self):
        # Compatibilidade: código antigo que constrói sem `origem`.
        self.assertEqual(orcamento_escola().origem, ORIGEM_ESCOLA)
        self.assertEqual(orcamento_escola().integra_pedido_produto_ids, [])


class TestEscolhaDoSql(unittest.TestCase):
    def test_integracao_usa_o_sql_proprio_e_o_parametro_de_produtos(self):
        self.assertEqual(
            _arquivo_e_parametro(orcamento_integracao()),
            (ARQUIVO_INTEGRACAO, PARAMETRO_IDS_INTEGRACAO),
        )

    def test_escola_continua_escolhendo_pelo_modo(self):
        self.assertEqual(
            _arquivo_e_parametro(orcamento_escola(modo_agrupamento="unidade")),
            ("orcamento_unidade.sql", PARAMETRO_IDS_ESCOLA),
        )
        self.assertEqual(
            _arquivo_e_parametro(orcamento_escola(modo_agrupamento="escola")),
            ("orcamento_escola.sql", PARAMETRO_IDS_ESCOLA),
        )

    def test_modo_desconhecido_na_escola_bloqueia(self):
        with self.assertRaises(PayloadIncompleto):
            _arquivo_e_parametro(orcamento_escola(modo_agrupamento="turma"))

    def test_integracao_nao_precisa_de_modo_de_agrupamento(self):
        # modo_agrupamento é NULL no lote de integração (CHECK do banco).
        conn = ConexaoFalsa([corpo_sql("9001", "9002")])
        montar_payload_orcamento(conn, orcamento_integracao(modo_agrupamento=None), "PageFlow")
        self.assertEqual(conn.parametros[PARAMETRO_IDS_INTEGRACAO], [9001, 9002])

    def test_o_sql_de_integracao_existe_e_declara_os_parametros(self):
        caminho = modulo_payload.PASTA_SQL / ARQUIVO_INTEGRACAO
        sql = caminho.read_text(encoding="utf-8-sig")
        # Só o SQL de verdade: os comentários explicam o que NÃO está lá e
        # fariam as asserções negativas darem falso positivo.
        corpo = "\n".join(
            linha for linha in sql.splitlines() if not linha.lstrip().startswith("--")
        )

        for parametro in (":integra_pedido_produto_ids", ":id_cliente", ":id_vendedor",
                          ":id_forma_pagamento", ":data_entrega"):
            self.assertIn(parametro, corpo)
        # obs_producao = descrição do pedido do parceiro + a data de entrega do
        # orçamento, no mesmo formato do orcamento_escola.sql. A descrição NÃO
        # pode se perder no caminho.
        self.assertIn("'Data de Entrega: '", corpo)
        self.assertIn("ip.descricao", corpo)
        self.assertIn("CONCAT_WS", corpo)
        # Correções pedidas: codigo_externo presente, sem pedido_ids nem identifier fixo.
        self.assertIn("'codigo_externo', pr.produto_id::text", corpo)
        self.assertNotIn("pedido_ids", corpo)
        self.assertNotIn("'identifier'", corpo)
        # Capa/miolo pelas flags do componente, não por LIKE na descrição.
        self.assertIn("bc.is_capa", corpo)
        self.assertIn("bc.is_miolo", corpo)
        self.assertNotIn("LIKE '%CAPA%'", corpo)
        # (e) catálogo já está em cm: nada de dividir por 10 como na escola.
        self.assertIn("'altura', cbmc.altura_padrao", corpo)
        self.assertNotIn("altura_padrao::numeric / 10", corpo)


class TestPayloadIntegracao(unittest.TestCase):
    def test_monta_com_identifier_e_os_produtos_do_orcamento(self):
        conn = ConexaoFalsa([corpo_sql("9001", "9002")])

        corpo = montar_payload_orcamento(conn, orcamento_integracao(), "PageFlow")

        self.assertEqual(corpo["identifier"], "PageFlow")
        self.assertEqual(len(corpo["data"]["itens"]), 2)
        self.assertEqual(conn.parametros[PARAMETRO_IDS_INTEGRACAO], [9001, 9002])
        self.assertNotIn(PARAMETRO_IDS_ESCOLA, conn.parametros)
        self.assertEqual(conn.parametros["id_cliente"], 3407)
        self.assertEqual(conn.parametros["id_forma_pagamento"], "1")
        # A data de entrega do orçamento vai ao SQL para entrar no obs_producao.
        self.assertEqual(conn.parametros["data_entrega"], "01/12/2026")

    def test_a_data_de_entrega_so_vai_ao_sql_na_origem_integracao(self):
        # Os SQLs de escola não declaram :data_entrega — mandar o parâmetro
        # para eles faria o SQLAlchemy reclamar de bind sobrando.
        conn = ConexaoFalsa([corpo_sql("1", "2")])

        montar_payload_orcamento(conn, orcamento_escola(), "PageFlow")

        self.assertNotIn("data_entrega", conn.parametros)

    def test_orcamento_de_integracao_sem_data_passa_none_ao_sql(self):
        # O CHECK do banco exige a data, mas o SQL trata NULL ('-') em vez de
        # derrubar o envio inteiro por um dado que o ERP nem usa para orçar.
        conn = ConexaoFalsa([corpo_sql("9001", "9002")])

        montar_payload_orcamento(conn, orcamento_integracao(data_entrega=None), "PageFlow")

        self.assertIsNone(conn.parametros["data_entrega"])

    def test_produto_descartado_pelo_sql_bloqueia_o_envio(self):
        conn = ConexaoFalsa([corpo_sql("9001")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento_integracao(), "PageFlow")
        self.assertIn("[9002]", str(ctx.exception))
        self.assertIn("produtos", str(ctx.exception))

    def test_produto_de_outro_orcamento_bloqueia_o_envio(self):
        conn = ConexaoFalsa([corpo_sql("9001,9002,9999")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento_integracao(), "PageFlow")
        self.assertIn("[9999]", str(ctx.exception))

    def test_codigo_externo_agrupado_tambem_vale_na_integracao(self):
        conn = ConexaoFalsa([corpo_sql("9001,9002")])
        corpo = montar_payload_orcamento(conn, orcamento_integracao(), "PageFlow")
        self.assertEqual(len(corpo["data"]["itens"]), 1)

    def test_sem_produto_vinculado_bloqueia_antes_do_sql(self):
        conn = ConexaoFalsa([corpo_sql("9001")])
        with self.assertRaises(PayloadIncompleto):
            montar_payload_orcamento(conn, orcamento_integracao(integra_pedido_produto_ids=[]), "PageFlow")
        self.assertIsNone(conn.parametros)

    def test_cabecalho_incompleto_aponta_o_cadastro_da_integracao(self):
        conn = ConexaoFalsa([corpo_sql("9001", "9002")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento_integracao(cliente_id=None), "PageFlow")
        mensagem = str(ctx.exception)
        self.assertIn("Integração sem cliente", mensagem)
        self.assertIn("cadastro da integração", mensagem)
        self.assertIsNone(conn.parametros)

    def test_mais_de_uma_linha_do_sql_bloqueia(self):
        conn = ConexaoFalsa([corpo_sql("9001"), corpo_sql("9002")])
        with self.assertRaises(PayloadIncompleto) as ctx:
            montar_payload_orcamento(conn, orcamento_integracao(), "PageFlow")
        self.assertIn("integração", str(ctx.exception))


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


def linha_integracao(id_op, produto_id, tipo, url):
    """Linha como `fila.arquivos_da_aprovacao` devolve para lote de integração."""
    return {
        "id_op": id_op,
        "pedido_distribuicao_id": None,
        "integra_pedido_produto_id": produto_id,
        "arquivo_pdf_id": None,
        "arquivo_nome": f"{produto_id}_{tipo}.pdf",
        "url": url,
        "tipo_arquivo": tipo,
        "chave_arquivo": f"{produto_id}:{tipo}",
        "pasta": "Parceiro X - ORD-42",
    }


class TestChaveArquivo(unittest.TestCase):
    def test_usa_chave_do_sql_quando_existe(self):
        self.assertEqual(chave_arquivo({"chave_arquivo": "9001:capa_frente"}), "9001:capa_frente")

    def test_cai_no_arquivo_pdf_id_na_origem_escola(self):
        self.assertEqual(chave_arquivo({"arquivo_pdf_id": 77}), "77")


class TestDownloadIntegracao(unittest.TestCase):
    def _baixar(self, arquivos, baixador=None):
        baixador = baixador or BaixadorFalso()
        base = tempfile.mkdtemp()
        motor = MotorFalso()
        repassador = RepassadorFalso()
        gravadas = []

        servico = DownloadArquivos(
            motor, baixador, repassador,
            configuracao(DOWNLOAD_BASE_PATH=base), dormir=lambda _s: None,
        )
        import deskflow2.servicos.download_arquivos as modulo

        original_arquivos = modulo.fila.arquivos_da_aprovacao
        original_registrar = modulo.fila.registrar_downloads_bremen
        modulo.fila.arquivos_da_aprovacao = lambda _conn, _id: arquivos
        modulo.fila.registrar_downloads_bremen = lambda _conn, linhas: gravadas.extend(linhas)
        try:
            resultado = servico.baixar_aprovacao(900)
        finally:
            modulo.fila.arquivos_da_aprovacao = original_arquivos
            modulo.fila.registrar_downloads_bremen = original_registrar
        return resultado, gravadas, base, baixador

    def test_baixa_pdf_e_designs_na_pasta_da_integracao(self):
        arquivos = [
            linha_integracao(5000, 9001, "miolo", "https://x.public.blob.vercel-storage.com/a.pdf"),
            linha_integracao(5000, 9001, "capa_frente", "https://x.public.blob.vercel-storage.com/b.pdf"),
            linha_integracao(5000, 9001, "capa_verso", "https://x.public.blob.vercel-storage.com/c.pdf"),
        ]

        resultado, gravadas, base, baixador = self._baixar(arquivos)

        self.assertTrue(resultado["sucesso"])
        self.assertEqual(resultado["total_arquivos"], 3)
        self.assertEqual(len(baixador.urls), 3)
        # Pasta: <base>/<integração - pedido>/<op>/ — a escola não entra aqui.
        pasta = os.path.join(base, "Parceiro X - ORD-42", "5000")
        self.assertTrue(os.path.isdir(pasta))
        self.assertEqual(
            sorted(os.listdir(pasta)),
            ["9001_capa_frente.pdf", "9001_capa_verso.pdf", "9001_miolo.pdf"],
        )

    def test_grava_downloads_bremen_no_lado_de_integracao_do_arco(self):
        arquivos = [linha_integracao(5000, 9001, "miolo", "https://x.public.blob.vercel-storage.com/a.pdf")]

        _resultado, gravadas, _base, _baixador = self._baixar(arquivos)

        self.assertEqual(len(gravadas), 1)
        linha = gravadas[0]
        # CHECK ck_downloads_bremen_origem: exatamente uma das duas colunas.
        self.assertIsNone(linha["distribuicao_material_id"])
        self.assertEqual(linha["integra_pedido_produto_id"], 9001)
        # Não há pedido_arquivos_pdf para produto de integração.
        self.assertIsNone(linha["arquivo_pdf_id"])
        self.assertEqual(linha["id_ops"], 5000)
        self.assertEqual(linha["tipo_arquivo"], "miolo")
        self.assertGreater(linha["tamanho"], 0)

    def test_produto_sem_design_baixa_so_o_pdf(self):
        # O SQL já não devolve linha para URL vazia; aqui o serviço só recebe
        # o que existe e não inventa arquivo nenhum.
        arquivos = [linha_integracao(5000, 9001, "miolo", "https://x.public.blob.vercel-storage.com/a.pdf")]

        resultado, gravadas, _base, baixador = self._baixar(arquivos)

        self.assertEqual(resultado["total_arquivos"], 1)
        self.assertEqual(len(baixador.urls), 1)
        self.assertEqual(len(gravadas), 1)

    def test_falha_de_download_vira_erro_e_nao_grava_a_linha(self):
        ruim = "https://x.public.blob.vercel-storage.com/b.pdf"
        arquivos = [
            linha_integracao(5000, 9001, "miolo", "https://x.public.blob.vercel-storage.com/a.pdf"),
            linha_integracao(5000, 9001, "capa_frente", ruim),
        ]

        resultado, gravadas, _base, _baixador = self._baixar(arquivos, BaixadorFalso(falhar=[ruim]))

        self.assertFalse(resultado["sucesso"])
        self.assertEqual(resultado["total_arquivos"], 1)
        self.assertEqual(len(resultado["erros"]), 1)
        self.assertEqual([linha["tipo_arquivo"] for linha in gravadas], ["miolo"])

    def test_nome_de_pasta_com_caractere_proibido_e_sanitizado(self):
        arquivo = linha_integracao(5000, 9001, "miolo", "https://x.public.blob.vercel-storage.com/a.pdf")
        arquivo["pasta"] = "Parceiro/X: ..\\teste"
        _resultado, _gravadas, base, _baixador = self._baixar([arquivo])

        # Um nível só, sem subir de pasta.
        self.assertTrue(os.path.isdir(os.path.join(base, "Parceiro_X_ .._teste", "5000")))


if __name__ == "__main__":
    unittest.main()
