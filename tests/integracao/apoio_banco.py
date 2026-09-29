"""Base dos testes de integração da fila, contra o Postgres de TESTING.

Claim com `SKIP LOCKED`, lease e reaper não podem ser testados com dublê: o
comportamento que interessa é do banco. Então estes testes falam com o banco de
verdade — e só com as tabelas `fila_*`.

Isolamento, que é a parte que não pode dar errado:

- cada teste cria os SEUS tipos, com código sorteado (`teste.fila.<hex>`), e
  os apaga no fim. Nenhum dos 10 tipos reais é tocado — em especial, nenhum é
  ativado por engano;
- cada teste apaga os itens que criou antes de apagar os tipos (a FK do tipo é
  RESTRICT), e os eventos saem em CASCADE junto com os itens;
- nada de tabela de domínio, nada de chamada ao ERP, nada de ciclo geral.

A suíte pula inteira, com mensagem, quando o banco não está alcançável — assim
`python -m unittest discover -s tests` continua valendo em máquina sem acesso.
"""

import os
import unittest
import uuid

from sqlalchemy import text

_engine = None
_motivo_skip = None


def _obter_engine():
    global _engine, _motivo_skip
    if _engine is not None or _motivo_skip is not None:
        return _engine
    try:
        from deskflow2.db import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM fila_processamento_status LIMIT 1"))
        _engine = engine
    except Exception as exc:  # pragma: no cover - depende do ambiente
        _motivo_skip = f"banco de testing indisponível ou sem as tabelas fila_*: {exc}"
    return _engine


class TesteDeFila(unittest.TestCase):
    """Cria o catálogo de tipos do teste, guarda os ids criados e limpa tudo."""

    @classmethod
    def setUpClass(cls):
        if os.environ.get("DESKFLOW2_SEM_BANCO"):
            raise unittest.SkipTest("DESKFLOW2_SEM_BANCO definido")
        cls.engine = _obter_engine()
        if cls.engine is None:
            raise unittest.SkipTest(_motivo_skip)
        from deskflow2.fila.catalogo import carregar_catalogo

        with cls.engine.connect() as conn:
            cls.catalogo = carregar_catalogo(conn)

    def setUp(self):
        self.tipos_criados = []
        self.itens_criados = []
        # `destino` é texto livre e entra no predicado do claim: um destino
        # sorteado por teste isola a reivindicação de qualquer outra linha da
        # tabela, inclusive de outra sessão rodando ao mesmo tempo.
        self.destino = f"teste_{uuid.uuid4().hex[:10]}"

    def tearDown(self):
        # Itens primeiro: a FK para o tipo é RESTRICT. Os eventos vão em
        # CASCADE junto com os itens.
        with self.engine.begin() as conn:
            if self.itens_criados:
                conn.execute(text("DELETE FROM fila_processamento WHERE id = ANY(:ids)"),
                             {"ids": self.itens_criados})
            if self.tipos_criados:
                conn.execute(text("DELETE FROM fila_processamento WHERE tipo_codigo = ANY(:codigos)"),
                             {"codigos": self.tipos_criados})
                conn.execute(text("DELETE FROM fila_processamento_tipos WHERE codigo = ANY(:codigos)"),
                             {"codigos": self.tipos_criados})

    # --- Fixtures -----------------------------------------------------------

    def criar_tipo(self, *, classe="assincrono", ativo=True, idempotente=False,
                   concorrencia_maxima=None, timeout_segundos=30, max_tentativas=3,
                   prioridade=500, destino=None, verificacao_propria=False) -> str:
        """`verificacao_propria` faz o tipo apontar `tipo_verificacao_codigo`
        para ele mesmo: basta para o motor considerar que existe verificador
        (quem verifica de fato é o handler), e a autorreferência mantém o
        `tearDown` simples — a FK RESTRICT não passa a cruzar duas linhas."""
        destino = destino or self.destino
        codigo = f"teste.fila.{uuid.uuid4().hex[:12]}"
        with self.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO fila_processamento_tipos
                    (codigo, nome, destino, classe_padrao, prioridade_padrao, max_tentativas_padrao,
                     timeout_segundos, concorrencia_maxima, idempotente, ativo, tipo_verificacao_codigo)
                VALUES (:codigo, :codigo, :destino, :classe, :prioridade, :max_tentativas,
                        :timeout, :concorrencia, :idempotente, :ativo,
                        CASE WHEN CAST(:verificacao AS boolean) THEN :codigo ELSE NULL END)
            """), {
                "codigo": codigo, "destino": destino, "classe": classe, "prioridade": prioridade,
                "max_tentativas": max_tentativas, "timeout": timeout_segundos,
                "concorrencia": concorrencia_maxima, "idempotente": idempotente, "ativo": ativo,
                "verificacao": verificacao_propria,
            })
        self.tipos_criados.append(codigo)
        return codigo

    def criar_item(self, tipo_codigo: str, *, classe="assincrono", prioridade=500,
                   chave_bloqueio=None, status="pendente", disponivel_em_segundos=0,
                   max_tentativas=3, tentativa=0, lease_segundos=None,
                   claim_por=None, destino=None, payload=None) -> int:
        import json

        from deskflow2.fila.catalogo import EXECUTANDO, RESERVADO

        destino = destino or self.destino

        lease = None if lease_segundos is None else float(lease_segundos)
        precisa_lease = status in (RESERVADO, EXECUTANDO)
        if precisa_lease and lease is None:
            lease = 60.0
        with self.engine.begin() as conn:
            item_id = conn.execute(text("""
                INSERT INTO fila_processamento
                    (tipo_codigo, classe, destino, prioridade, status_id, origem, payload,
                     disponivel_em, max_tentativas, tentativa, chave_bloqueio, claim_por, claim_em,
                     lease_expira_em, iniciado_em)
                VALUES (:tipo, :classe, :destino, :prioridade, :status, 'teste',
                        CAST(:payload AS jsonb),
                        now() + make_interval(secs => CAST(:atraso AS double precision)),
                        :max_tentativas, :tentativa,
                        CAST(:chave AS varchar), CAST(:claim_por AS varchar),
                        CASE WHEN CAST(:claim_por AS varchar) IS NULL THEN NULL ELSE now() END,
                        CASE WHEN CAST(:lease AS double precision) IS NULL THEN NULL
                             ELSE now() + make_interval(secs => CAST(:lease AS double precision)) END,
                        CASE WHEN CAST(:claim_por AS varchar) IS NULL THEN NULL ELSE now() END)
                RETURNING id
            """), {
                "tipo": tipo_codigo, "classe": classe, "destino": destino, "prioridade": prioridade,
                "status": self.catalogo.id_status(status), "atraso": float(disponivel_em_segundos),
                "max_tentativas": max_tentativas, "tentativa": tentativa, "chave": chave_bloqueio,
                "claim_por": claim_por or ("worker-teste" if precisa_lease else None),
                "lease": lease, "payload": json.dumps(payload or {}),
            }).scalar()
        self.itens_criados.append(item_id)
        return item_id

    # --- Leitura ------------------------------------------------------------

    def status_de(self, item_id: int) -> str:
        with self.engine.connect() as conn:
            codigo = conn.execute(text("""
                SELECT s.codigo FROM fila_processamento f
                  JOIN fila_processamento_status s ON s.id = f.status_id
                 WHERE f.id = :id
            """), {"id": item_id}).scalar()
        return codigo

    def linha(self, item_id: int) -> dict:
        with self.engine.connect() as conn:
            return dict(conn.execute(text("SELECT * FROM fila_processamento WHERE id = :id"),
                                     {"id": item_id}).mappings().one())

    def eventos_de(self, item_id: int) -> list:
        with self.engine.connect() as conn:
            return [linha["evento"] for linha in conn.execute(text("""
                SELECT evento FROM fila_processamento_eventos
                 WHERE fila_item_id = :id ORDER BY em, id
            """), {"id": item_id}).mappings()]

    def reivindicar(self, classe="assincrono", worker="worker-a", lote=10, margem=30,
                    destino=None):
        from deskflow2.fila import repositorio

        with self.engine.begin() as conn:
            return repositorio.reivindicar(
                conn, self.catalogo, classe, destino or self.destino, worker, lote, margem)
