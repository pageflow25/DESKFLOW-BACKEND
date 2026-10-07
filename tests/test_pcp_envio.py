import unittest
from datetime import date
from unittest.mock import MagicMock

from pydantic import ValidationError

from app.schemas.orcamento import OrcamentoRequest, FluxoOrcamentoRequest, GerarOrcamentoCompleto
from app.services.orcamento_service import OrcamentoService


TITULO_ORIGINAL = '(*Turma A) - ARQUIVO ORIGINAL - (#50)'


def item(formulario, ids, titulo=TITULO_ORIGINAL, obs=None, escola=False):
    dados = {'_formulario_id': formulario, 'titulo': titulo, 'obs_producao': obs,
             'id_produto': 1, 'quantidade': 10}
    if escola:
        dados['ids_distribuicao'] = ids
    else:
        dados['componentes'] = [{'id': 1, 'descricao': 'Miolo', 'id_distribuicao': did} for did in ids]
    return dados


class EnvioPCPTest(unittest.TestCase):
    def aplicar(self, itens_por_orcamento, totais, nomes=None):
        db = MagicMock()
        db.execute.return_value.all.return_value = totais
        rows = [({'data': {'itens': itens}},) for itens in itens_por_orcamento]
        OrcamentoService.aplicar_observacoes_pcp(db, rows, nomes or {})
        return db

    def test_total_entre_unidades_nao_e_parcial(self):
        a, b = item(50, [1, 1]), item(50, [2])
        db = self.aplicar([[a], [b]], [(50, 1), (50, 2)])
        self.assertIsNone(a['obs_producao'])
        self.assertIsNone(b['obs_producao'])
        self.assertEqual(a['titulo'], TITULO_ORIGINAL)
        self.assertNotIn('_formulario_id', a)
        db.execute.assert_called_once()

    def test_parcial_preserva_observacao(self):
        a = item(50, [1], obs='Manter acabamento original')
        self.aplicar([[a]], [(50, 1), (50, 2)])
        self.assertEqual(a['obs_producao'], 'Manter acabamento original\nparcial: true')
        self.assertEqual(a['titulo'], TITULO_ORIGINAL)

    def test_parcial_acrescenta_nome_pcp_sem_remover_titulo_original(self):
        a = item(50, [1])
        self.aplicar([[a]], [(50, 1), (50, 2)], {50: 'Apostila Ensino Médio - Turma A'})
        self.assertEqual(a['titulo'], f'{TITULO_ORIGINAL} - Apostila Ensino Médio - Turma A')
        self.assertEqual(a['obs_producao'],
                         "parcial: true\nnome_pcp_alterado: 'Apostila Ensino Médio - Turma A'")

    def test_total_renomeado_registra_alteracao(self):
        a = item(50, [1], escola=True)
        self.aplicar([[a]], [(50, 1)], {50: 'Nome PCP'})
        self.assertEqual(a['titulo'], f'{TITULO_ORIGINAL} - Nome PCP')
        self.assertEqual(a['obs_producao'], "nome_pcp_alterado: 'Nome PCP'")

    def test_lote_misto_isola_formularios_e_modos(self):
        a, b = item(50, [1], escola=True), item(60, [3], escola=True)
        self.aplicar([[a, b]], [(50, 1), (50, 2), (60, 3)], {50: 'Novo'})
        self.assertIn('parcial: true', a['obs_producao'])
        self.assertIsNone(b['obs_producao'])
        self.assertEqual(b['titulo'], TITULO_ORIGINAL)

    def test_nome_yaml_escapa_aspas_dois_pontos_e_hashtag(self):
        a = item(50, [1])
        self.aplicar([[a]], [(50, 1)], {50: "João's: turma #A"})
        self.assertEqual(a['obs_producao'], "nome_pcp_alterado: 'João''s: turma #A'")
        self.assertEqual(a['titulo'], f"{TITULO_ORIGINAL} - João's: turma #A")

    def test_nome_igual_ao_arquivo_nao_registra_alteracao(self):
        a = item(50, [1])
        self.aplicar([[a]], [(50, 1)], {50: a['titulo']})
        self.assertIsNone(a['obs_producao'])

    def test_sem_itens_nao_consulta_banco(self):
        db = self.aplicar([], [], {99: 'Fora da seleção'})
        db.execute.assert_not_called()

    def test_validacao_em_todos_os_contratos(self):
        base = dict(escola_id=1, ids_produtos=[1], datas_saida=[date(2026, 10, 6)],
                    tipo_fluxo='com_distribuicao_sem_faturamento')
        for schema in (OrcamentoRequest, FluxoOrcamentoRequest, GerarOrcamentoCompleto):
            with self.subTest(schema=schema.__name__):
                request = schema(**base, nomes_pcp_alterados={'50': '  Novo nome  '}, ids_distribuicoes=[1])
                self.assertEqual(request.nomes_pcp_alterados, {50: 'Novo nome'})
                for nomes in ({0: 'Nome'}, {50: ''}, {50: 'x' * 256}, {50: 'nome\nparcial: false'}, {50: 123}):
                    with self.assertRaises(ValidationError):
                        schema(**base, nomes_pcp_alterados=nomes)
                with self.assertRaises(ValidationError):
                    schema(**base, ids_distribuicoes=[0])

    def test_geracao_aplica_regras_antes_do_schema(self):
        for modo in ('unidade', 'escola'):
            with self.subTest(modo=modo):
                a = item(50, [1], escola=modo == 'escola')
                db = MagicMock()
                db.execute.return_value.fetchall.return_value = [({'identifier': 'PageFlow', 'data': {'itens': [a]}},)]
                db.execute.return_value.all.return_value = [(50, 1), (50, 2)]
                request = OrcamentoRequest(escola_id=1, ids_produtos=[1], datas_saida=[date(2026, 10, 6)],
                                           modo_agrupamento=modo, nomes_pcp_alterados={50: 'PCP'}, ids_distribuicoes=[1])
                resposta = OrcamentoService.gerar_orcamento(db, request)
                enviado = resposta.orcamentos[0].data.itens[0]
                self.assertEqual(enviado.titulo, f'{TITULO_ORIGINAL} - PCP')
                self.assertIn('parcial: true', enviado.obs_producao)
                self.assertNotIn('_formulario_id', enviado.model_dump())
                self.assertEqual(db.execute.call_args_list[0].args[1]['ids_distribuicoes'], [1])


if __name__ == '__main__':
    unittest.main()
