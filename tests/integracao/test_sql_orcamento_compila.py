"""Os SQLs de orçamento têm de COMPILAR contra o banco de verdade.

Por que este arquivo existe: a migration 20260923110000-contract-papel-antigo do
PageFlow dropou `pedido_especificacoes.id_papel` e moveu `bremen_tamanho_papel`
para o schema `contract_backup`. Os SQLs de `orcamento_escola` e
`orcamento_unidade` ainda traziam um `LEFT JOIN bremen_tamanho_papel` como
reserva para pedidos antigos — e um LEFT JOIN não fica "vazio" quando a tabela
não existe, ele derruba a query inteira com `relation does not exist`. O ciclo
antigo do PCP já falhava assim, e a falha só aparecia no momento do envio.

Nenhum teste de dublê pega isso: nome de tabela e de coluna só são conferidos
por quem tem o catálogo, que é o Postgres. `PREPARE` faz exatamente essa
conferência, e faz de graça — valida tabelas, colunas e tipos SEM executar a
query, sem ler uma linha e sem abrir transação de escrita.

O teste é genérico de propósito: pega todo `deskflow2/sql/orcamento_*.sql` que
existir. SQL novo entra coberto sem ninguém lembrar de acrescentá-lo aqui.
"""

import pathlib
import re

from .apoio_banco import TesteDeFila

SQL_DIR = pathlib.Path(__file__).resolve().parents[2] / "deskflow2" / "sql"

# Tipo de cada parâmetro nomeado, porque PREPARE exige a assinatura. O que não
# estiver aqui entra como `text`, que basta para a validação de nomes.
TIPOS_DE_PARAMETRO = {
    "pedido_distribuicao_ids": "int[]",
    "integra_pedido_produto_ids": "int[]",
    "id_cliente": "int",
    "id_vendedor": "int",
    "id_forma_pagamento": "int",
    "id_turma": "int",
    "unidade_escolar_id": "int",
    "integracao_id": "int",
    "integra_pedido_id": "int",
    "aprovacao_id": "int",
}

# Os SQLs conferidos: todo `orcamento_*.sql` e o da aprovação.
def _sqls():
    return sorted(SQL_DIR.glob("orcamento_*.sql")) + [SQL_DIR / "aprovacao.sql"]


# `:nome` de parâmetro, nunca o `::tipo` de um cast nem o segundo colon dele.
PARAMETRO = re.compile(r"(?<![:\w]):([a-z_][a-z0-9_]*)")


def _para_posicionais(sql: str):
    """Troca `:nome` por `$1..$n` na ordem de primeira aparição."""
    nomes = []
    for nome in PARAMETRO.findall(sql):
        if nome not in nomes:
            nomes.append(nome)

    corpo = sql
    for posicao, nome in enumerate(nomes, start=1):
        corpo = re.sub(r"(?<![:\w]):%s\b" % nome, "$%d" % posicao, corpo)
    return corpo, nomes


class TestSqlDeOrcamentoCompila(TesteDeFila):
    def test_existem_sqls_de_orcamento_para_conferir(self):
        # Sem isto, renomear a pasta faria a suíte passar sem conferir nada.
        arquivos = [arquivo for arquivo in _sqls() if arquivo.exists()]
        self.assertGreaterEqual(len(arquivos), 4, f"esperados ao menos 3 SQLs em {SQL_DIR}")

    def test_o_postgres_aceita_cada_sql_de_orcamento(self):
        arquivos = _sqls()
        falhas = []

        for arquivo in arquivos:
            corpo, nomes = _para_posicionais(arquivo.read_text(encoding="utf-8"))
            assinatura = ", ".join(TIPOS_DE_PARAMETRO.get(nome, "text") for nome in nomes)
            rotulo = "verificacao_" + re.sub(r"[^a-z0-9_]", "_", arquivo.stem)

            bruta = self.engine.raw_connection()
            try:
                cursor = bruta.cursor()
                try:
                    cursor.execute("DEALLOCATE ALL")
                    cursor.execute(f"PREPARE {rotulo} ({assinatura}) AS {corpo}")
                except Exception as erro:
                    falhas.append(f"{arquivo.name}: {str(erro).strip().splitlines()[0]}")
                finally:
                    # PREPARE não escreve nada; o rollback é só higiene de sessão.
                    bruta.rollback()
            finally:
                bruta.close()

        self.assertEqual(falhas, [], "SQL de orcamento que o Postgres recusa:\n" + "\n".join(falhas))

    def test_nenhum_sql_referencia_o_modelo_de_papel_antigo(self):
        """Complementa o PREPARE com a razão do problema, não só o sintoma.

        A tabela e a coluna saíram do schema `public`; uma referência nova a
        elas volta a quebrar o envio inteiro, e a mensagem do PREPARE (`relation
        does not exist`) não diz de onde veio.
        """
        proibidos = ("bremen_tamanho_papel", "bremen_item_tamanho_papel", "id_papel")
        achados = []

        for arquivo in sorted(SQL_DIR.glob("*.sql")):
            for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), start=1):
                if linha.lstrip().startswith("--"):
                    continue  # comentário pode citar o histórico
                for proibido in proibidos:
                    if re.search(r"\b%s\b" % proibido, linha):
                        achados.append(f"{arquivo.name}:{numero} usa {proibido}")

        self.assertEqual(achados, [], "modelo de papel antigo (dropado em 2026-09-23):\n" + "\n".join(achados))
