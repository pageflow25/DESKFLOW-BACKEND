"""Componentes POR ESPECIFICAÇÃO nos SQLs de orçamento de escola (unidade e escola).

O PageFlow grava uma especificação por componente, com o papel em três eixos já
resolvido (ver BACKEND_PAGEFLOW/docs/papel-tres-eixos-deskflow.md): capa com o
papel dela, folha de rosto herdando do miolo, dobra aplicada no substrato — e,
desde 2026-09-24, também os componentes SEM arquivo (papelão, guarda), marcados
com `metadados.sem_arquivo = true`. O orçamento monta cada componente da
própria especificação, sem cenário por tipo nem regra por categoria de produto.
A única exceção é a cor da capa, que não vai (a gravada é a do PDF/item).

Os casos espelham BACKEND_PAGEFLOW/docs/papel-tres-eixos-casos-de-teste.md e o
pedido 13340 do testing (apostila capa dura, item 107).

Como roda sem sujar o banco compartilhado: as tabelas `pedido_*` que os SQLs
leem são SOMBREADAS por tabelas TEMP com o mesmo nome (o Postgres procura em
`pg_temp` antes de `public`), e tudo sofre rollback. Só o cadastro Bremen
(`bremen_itens`, `bremen_componentes`, `bremen_gramatura`) é o real — se
alguém reconfigurar os itens usados aqui, reconfira antes de acusar bug.
"""

import pathlib

from sqlalchemy import text

from .apoio_banco import TesteDeFila

SQL_DIR = pathlib.Path(__file__).resolve().parents[2] / "deskflow2" / "sql"

TABELAS_TEMP = """
CREATE TEMP TABLE pedido_formularios (id int, observacoes text, data_entrega date, titulo text,
                                      criado_em timestamptz, usuario_id int);
CREATE TEMP TABLE pedido_distribuicoes (id int, unidade_escolar_id int, formulario_id int,
                                        pedido_item_carrinho_id int, quantidade int, id_turma int);
CREATE TEMP TABLE pedido_arquivos_pdf (id int, nome text, paginas int);
CREATE TEMP TABLE pedido_distribuicao_arquivos (distribuicao_material_id int, arquivo_pdf_id int,
                                                id_componente int, especificacao_form_id int);
CREATE TEMP TABLE pedido_especificacoes (id int, id_produto int, id_componente int, corfrente int,
                                         corverso int, gramatura_miolo text, id_gramatura int,
                                         id_formato int, id_tipo_papel int, id_substrato int,
                                         altura text, largura text, pedido_item_carrinho_id int,
                                         metadados text)
"""

COM_ARQUIVO, SEM_ARQUIVO, ORFA = "arquivo", "sem_arquivo", "orfa"

# (distribuição, componente, produto, formato, tipo, substrato, gramatura, cor, origem)
# — o que o PageFlow grava para cada caso. `origem`:
#   arquivo     — tem PDF distribuído (pedido_distribuicao_arquivos);
#   sem_arquivo — componente sem PDF, com a marca `sem_arquivo` (papelão, guarda);
#   orfa        — especificação do carrinho sem PDF e sem a marca (legado jun–ago).
ESPECIFICACOES = [
    # CT-01 / SC-03 — item 85, capa dura: capa Couchê 170, miolo Offset A4 75.
    (1, 218, 85, 11, 3, 124, 170, (1, 1), COM_ARQUIVO), (1, 219, 85, 11, 9, 109, 75, (1, 1), COM_ARQUIVO),
    # CT-03 / SC-05 — item 272, livreto dobrado A4 200x275: a dobra vira A3 (sub 118).
    (2, 553, 272, 8, 4, 118, 75, (1, 1), COM_ARQUIVO),
    # CT-04 / SC-04 — item 101: folha de rosto (is_capa, sem vínculo) herda do miolo.
    (3, 258, 101, 11, 9, 109, 75, (1, 0), COM_ARQUIVO), (3, 259, 101, 11, 9, 109, 75, (1, 0), COM_ARQUIVO),
    # Livreto "Livreto - Grupo Salta" (256): a regra antiga só reconhecia 'LIVRETO'.
    (4, 529, 256, 3, 9, 109, 75, (1, 1), COM_ARQUIVO), (4, 530, 256, 3, 9, 109, 75, (1, 1), COM_ARQUIVO),
    # Item 35: componente único que não é capa nem miolo ("Folder").
    (5, 92, 35, 11, 9, 109, 75, (4, 4), COM_ARQUIVO),
    # Especificação sem papel resolvido: as chaves de papel ficam de fora.
    (6, 218, 85, None, None, None, None, (1, 1), COM_ARQUIVO),
    (6, 219, 85, None, None, None, None, (1, 1), COM_ARQUIVO),
    # Pedido 13340 (item 107): capa e miolo com PDF, papelão e guarda sem PDF,
    # e uma especificação órfã de capa no mesmo carrinho que não pode entrar.
    (7, 274, 107, 11, 3, 124, 170, (4, 4), COM_ARQUIVO), (7, 275, 107, 11, 9, 109, 75, (4, 4), COM_ARQUIVO),
    (7, 276, 107, 11, 227, 89, 1043, (0, 0), SEM_ARQUIVO), (7, 277, 107, 11, 15, 110, 180, (0, 0), SEM_ARQUIVO),
    (7, 274, 107, 11, 9, 109, 75, (1, 1), ORFA),
    # "Capa + Miolo" (item 126): is_capa E is_miolo — é o PDF inteiro, a cor vai.
    (8, 320, 126, 11, 15, 110, 120, (4, 4), COM_ARQUIVO),
]


def _papel(componente):
    return (componente.get("idgruposubstratoimpressao"), componente.get("gramaturasubstratoimpressao"))


def _cor(componente):
    return (componente.get("corfrente"), componente.get("corverso"))


class TestComponentesPorEspecificacaoNoOrcamento(TesteDeFila):
    def _componentes(self, arquivo):
        """{distribuição: {id_componente: componente}} do SQL, sobre as fixtures."""
        sql = (SQL_DIR / arquivo).read_text(encoding="utf-8")
        resultado = {}
        with self.engine.connect() as conn:
            try:
                for comando in TABELAS_TEMP.split(";"):
                    conn.execute(text(comando))
                conn.execute(text("INSERT INTO pedido_formularios VALUES (1, NULL, '2026-10-10', 'T', now(), NULL)"))
                for posicao, (d, comp, prod, formato, tipo, substrato, gramatura, cor, origem) in enumerate(ESPECIFICACOES):
                    espec = d * 1000 + posicao
                    conn.execute(text("""
                        INSERT INTO pedido_especificacoes
                        SELECT :espec, :prod, :comp, :corfrente, :corverso, CAST(:gramatura AS int) || ' g',
                               (SELECT id FROM bremen_gramatura WHERE gramatura = :gramatura),
                               :formato, :tipo, :substrato,
                               CASE WHEN :formato IS NULL THEN '297' END,
                               CASE WHEN :formato IS NULL THEN '210' END,
                               :carrinho, :metadados
                    """), {"espec": espec, "prod": prod, "comp": comp, "gramatura": gramatura,
                           "formato": formato, "tipo": tipo, "substrato": substrato,
                           "corfrente": cor[0], "corverso": cor[1], "carrinho": d,
                           # JSON.stringify do PageFlow: sem espaço depois dos dois-pontos.
                           "metadados": '{"componente":"x","sem_arquivo":true}' if origem == SEM_ARQUIVO
                                        else '{"componente":"x"}'})
                    if origem == COM_ARQUIVO:
                        conn.execute(text("INSERT INTO pedido_arquivos_pdf VALUES (:id, :nome, 4)"),
                                     {"id": espec, "nome": f"arquivo_{espec}.pdf"})
                        conn.execute(text("INSERT INTO pedido_distribuicao_arquivos VALUES (:d, :id, :comp, :id)"),
                                     {"d": d, "id": espec, "comp": comp})
                distribuicoes = sorted({linha[0] for linha in ESPECIFICACOES})
                for d in distribuicoes:
                    conn.execute(text("INSERT INTO pedido_distribuicoes VALUES (:d, NULL, 1, :d, 10, NULL)"), {"d": d})

                for d in distribuicoes:
                    payloads = conn.execute(text(sql), {
                        "pedido_distribuicao_ids": [d], "id_cliente": 1, "id_vendedor": 1,
                        "id_forma_pagamento": "1", "data_entrega": None,
                    }).scalars().all()
                    self.assertEqual(len(payloads), 1, f"{arquivo}: distribuição {d}")
                    componentes = payloads[0]["data"]["itens"][0]["componentes"]
                    ids = [c["id"] for c in componentes]
                    self.assertEqual(len(ids), len(set(ids)), f"{arquivo}: componente repetido em {d}: {ids}")
                    resultado[d] = {c["id"]: c for c in componentes}
            finally:
                conn.rollback()
        return resultado

    def _conferir(self, arquivo):
        r = self._componentes(arquivo)
        with self.subTest(arquivo=arquivo, caso="capa com papel próprio, não o do miolo"):
            self.assertEqual(_papel(r[1][218]), (124, 170.0))
            self.assertEqual(_papel(r[1][219]), (109, 75.0))
        with self.subTest(arquivo=arquivo, caso="livreto dobrado leva o substrato da dobra"):
            self.assertEqual(_papel(r[2][553]), (118, 75.0))
            # Medida continua o tamanho FINAL (200x275), não o de produção.
            self.assertEqual((r[2][553]["largura"], r[2][553]["altura"]), (20.0, 27.5))
        with self.subTest(arquivo=arquivo, caso="folha de rosto herda o papel do miolo"):
            self.assertEqual(_papel(r[3][259]), _papel(r[3][258]))
            self.assertEqual(_papel(r[3][259]), (109, 75.0))
        with self.subTest(arquivo=arquivo, caso="capa de livreto fora da categoria 'Livreto'"):
            self.assertEqual(_papel(r[4][529]), (109, 75.0))
        with self.subTest(arquivo=arquivo, caso="componente único que não é capa nem miolo"):
            self.assertEqual(_papel(r[5][92]), (109, 75.0))
            self.assertEqual(_cor(r[5][92]), (4, 4))
        with self.subTest(arquivo=arquivo, caso="sem papel resolvido: chaves omitidas"):
            for componente in r[6].values():
                self.assertNotIn("idgruposubstratoimpressao", componente)
                self.assertNotIn("gramaturasubstratoimpressao", componente)
        with self.subTest(arquivo=arquivo, caso="componentes sem arquivo entram com o papel deles"):
            self.assertEqual(set(r[7]), {274, 275, 276, 277})
            self.assertEqual(_papel(r[7][276]), (89, 1043.0))
            self.assertEqual(_cor(r[7][276]), (0, 0))
            self.assertEqual(_papel(r[7][277]), (110, 180.0))
            self.assertNotIn("quantidade_paginas", r[7][276])
            self.assertEqual(r[7][275]["quantidade_paginas"], 4)
        with self.subTest(arquivo=arquivo, caso="especificação órfã do carrinho não substitui a com arquivo"):
            self.assertEqual(_papel(r[7][274]), (124, 170.0))
        with self.subTest(arquivo=arquivo, caso="capa não leva cor; miolo leva a dele"):
            self.assertEqual(_cor(r[7][274]), (None, None))
            self.assertEqual(_cor(r[7][275]), (4, 4))
            self.assertEqual(_cor(r[3][259]), (None, None))
        with self.subTest(arquivo=arquivo, caso="'Capa + Miolo' leva a cor (é o PDF inteiro)"):
            self.assertEqual(_cor(r[8][320]), (4, 4))

    def test_orcamento_unidade(self):
        self._conferir("orcamento_unidade.sql")

    def test_orcamento_agrupado(self):
        self._conferir("orcamento_agrupado.sql")
