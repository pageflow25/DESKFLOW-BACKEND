"""O ciclo de vida de um item, do claim ao estado final.

O motor não conhece nenhum handler nem nenhum destino: ele pede um handler ao
registry pelo `tipo_codigo`, recebe um `Preparo`, executa a chamada fora de
qualquer transação com o lease renovado, e grava o `Desfecho`.

A única coisa que ele conhece do mundo externo é a classificação de falha que o
`ErpClient` já fazia — `ErpIndisponivel` (certeza de que o destino NÃO
processou) contra `ResultadoIncerto` (pode ter processado). Ela é o vocabulário
da seção 5 do plano e vira estado persistido aqui.
"""

import logging
import random
import threading
import time
from typing import Optional

from ..integracoes.erp import ErpIndisponivel, ResultadoIncerto, modo_fila
from . import repositorio
from .catalogo import Catalogo, CLASSE_SINCRONO, EXECUTANDO, RESERVADO
from .modelos import Desfecho, Estado, ItemReivindicado, Preparo
from .registry import Registry

logger = logging.getLogger(__name__)

# Espera antes de um tipo ativo sem handler voltar a ser oferecido.
ATRASO_SEM_HANDLER_SEGUNDOS = 60.0


def backoff_com_jitter(tentativa: int, base: float, teto: float, sorteio=random.uniform) -> float:
    """Jitter COMPLETO: `uniform(0, min(base * 2^(n-1), teto))`.

    Não é `±10 %`. Quando o destino volta de uma queda há dezenas de itens
    prontos ao mesmo tempo; jitter parcial os reagrupa e derruba o destino de
    novo. O valor sai gravado em `disponivel_em` — nenhuma thread dorme.
    """
    expoente = max(0, tentativa - 1)
    limite = min(base * (2 ** expoente), teto)
    return sorteio(0.0, max(0.0, limite))


class _RenovadorDeLease:
    """Renova o lease em segundo plano enquanto a chamada está em voo.

    Uma thread daemon por item em execução, dormindo a maior parte do tempo.
    Sem isso, uma chamada mais lenta que o `timeout_segundos` do tipo seria
    colhida pelo reaper enquanto ainda está viva.
    """

    def __init__(self, engine, item_id: int, worker: str, lease_segundos: float):
        self._engine = engine
        self._item_id = item_id
        self._worker = worker
        self._lease = lease_segundos
        self._intervalo = max(1.0, lease_segundos / 3.0)
        self._parar = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._laco, name=f"lease-{self._item_id}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False

    def _laco(self) -> None:
        while not self._parar.wait(self._intervalo):
            try:
                with self._engine.begin() as conn:
                    if not repositorio.renovar_lease(conn, self._item_id, self._worker, self._lease):
                        return
            except Exception:
                # Renovação é best-effort: se o banco oscilar, o reaper decide.
                logger.warning("Fila: falha ao renovar lease do item %s", self._item_id, exc_info=True)


class Motor:
    def __init__(self, engine, catalogo: Catalogo, registry: Registry, settings, worker_id: str):
        self._engine = engine
        self._catalogo = catalogo
        self._registry = registry
        self._settings = settings
        self._worker_id = worker_id
        self.ator = f"deskflow:{worker_id}"

    # --- Backoff por classe -------------------------------------------------

    def atraso(self, item: ItemReivindicado) -> float:
        if item.classe == CLASSE_SINCRONO:
            base = self._settings.FILA_BACKOFF_SINCRONO_BASE
            teto = self._settings.FILA_BACKOFF_SINCRONO_TETO
        else:
            base = self._settings.FILA_BACKOFF_ASSINCRONO_BASE
            teto = self._settings.FILA_BACKOFF_ASSINCRONO_TETO
        return backoff_com_jitter(item.tentativa, base, teto)

    def lease_segundos(self, item: ItemReivindicado) -> float:
        tipo = self._catalogo.tipo(item.tipo_codigo)
        timeout = tipo.timeout_segundos if tipo else self._settings.FILA_LEASE_MARGEM_SEGUNDOS
        return float(timeout + self._settings.FILA_LEASE_MARGEM_SEGUNDOS)

    # --- Execução -----------------------------------------------------------

    def processar(self, item: ItemReivindicado) -> str:
        """Leva UM item reivindicado até um estado que não ocupa mais worker.
        Devolve o código do estado final (string vazia quando outra transição
        chegou antes — reaper ou cancelamento)."""
        handler = self._registry.obter(item.tipo_codigo)
        if handler is None:
            logger.error(
                "Fila: tipo '%s' está ativo no banco mas não tem handler neste worker; item %s devolvido",
                item.tipo_codigo, item.id,
            )
            with self._engine.begin() as conn:
                repositorio.liberar_sem_handler(conn, self._catalogo, item, ATRASO_SEM_HANDLER_SEGUNDOS, self.ator)
            return "sem_handler"

        lease = self.lease_segundos(item)
        try:
            preparo = self._preparar(item, handler, lease)
        except Exception as exc:
            # Falha ANTES de qualquer I/O: repetir é seguro por construção.
            logger.exception("Fila: preparo do item %s falhou", item.id)
            return self._aplicar(item, Desfecho.retentar(f"falha ao preparar: {exc}", "PREPARO"),
                                 de_status=self._catalogo.id_status(RESERVADO), duracao_ms=None)
        if preparo is None:
            return ""

        inicio = time.monotonic()
        desfecho = self._chamar(item, handler, preparo, lease)
        if desfecho.estado is Estado.INCERTO:
            desfecho = self._verificar(item, handler, desfecho, lease)
        duracao_ms = int((time.monotonic() - inicio) * 1000)
        return self._aplicar(item, desfecho, de_status=self._catalogo.id_status(EXECUTANDO),
                             duracao_ms=duracao_ms)

    def _verificar(self, item: ItemReivindicado, handler, incerto: Desfecho, lease: float) -> Desfecho:
        """Última chance de um resultado incerto se resolver sozinho.

        O tipo declara em `tipo_verificacao_codigo` que existe uma chave natural
        capaz de dizer se a chamada aconteceu (`cliente.consultar` verifica
        `cliente.criar`); o handler sabe fazer a pergunta. O motor só liga os
        dois — continua sem conhecer destino nenhum.

        `None` do handler, tipo sem verificador ou exceção mantêm o `incerto`:
        verificação que não pôde ser concluída NÃO é prova de nada, e transformar
        "não sei" em "não aconteceu" é o falso negativo que este desenho existe
        para eliminar. O item cai na decisão humana, como cairia antes.

        Roda FORA de transação, pelo mesmo motivo do `_chamar`: há I/O externa
        em voo. O `conn` do protocolo vai `None`.
        """
        verificar = getattr(handler, "verificar", None)
        if not callable(verificar):
            return incerto
        tipo = self._catalogo.tipo(item.tipo_codigo)
        if tipo is None or not tipo.tipo_verificacao_codigo:
            return incerto

        try:
            with _RenovadorDeLease(self._engine, item.id, self._worker_id, lease), modo_fila(item.classe):
                resolvido = verificar(None, item)
        except Exception:
            logger.exception("Fila: verificação do item %s falhou; segue incerto", item.id)
            return incerto
        if resolvido is None:
            logger.warning("Fila: item %s segue incerto — a verificação não decidiu", item.id)
            return incerto

        logger.info("Fila: item %s resolvido por verificação (%s -> %s)",
                    item.id, tipo.tipo_verificacao_codigo, resolvido.estado.value)
        try:
            with self._engine.begin() as conn:
                repositorio.registrar_evento(
                    conn, item.id, "verificacao", self.ator, item.tentativa, detalhe={
                        "verificador": tipo.tipo_verificacao_codigo,
                        "desfecho": resolvido.estado.value,
                        "incerto_por": incerto.erro_codigo,
                    })
        except Exception:
            # Timeline é auditoria, não pode derrubar o desfecho já decidido.
            logger.warning("Fila: não foi possível registrar a verificação do item %s", item.id, exc_info=True)
        return resolvido

    def _preparar(self, item: ItemReivindicado, handler, lease: float) -> Optional[Preparo]:
        """Transação curta: monta o corpo, grava `payload_enviado` e passa para
        `executando`. Sem I/O externa aqui dentro."""
        with self._engine.begin() as conn:
            preparo = handler.preparar(conn, item) or Preparo()
            repositorio.gravar_payload_enviado(conn, item.id, preparo.payload_enviado)
            if not repositorio.marcar_executando(conn, self._catalogo, item, lease, self.ator):
                logger.info("Fila: item %s não está mais reservado; outra transição chegou antes", item.id)
                return None
        return preparo

    def _chamar(self, item: ItemReivindicado, handler, preparo: Preparo, lease: float) -> Desfecho:
        """A chamada externa, FORA de qualquer transação, com o lease renovado."""
        if preparo.chamar is None:
            return Desfecho.falhou("handler não devolveu chamada a executar", "SEM_CHAMADA")

        tipo = self._catalogo.tipo(item.tipo_codigo)
        idempotente = bool(tipo and tipo.idempotente)
        try:
            # `modo_fila` liga a reserva de classe do semáforo do ErpClient e
            # tira a espera de 503 da thread — só aqui dentro, só nesta
            # chamada. Fora deste bloco o cliente é o de sempre.
            with _RenovadorDeLease(self._engine, item.id, self._worker_id, lease), modo_fila(item.classe):
                bruto = preparo.chamar()
            return handler.interpretar(item, bruto)
        except ErpIndisponivel as exc:
            # Certeza de que o destino não processou.
            return Desfecho.retentar(str(exc), "DESTINO_INDISPONIVEL")
        except ResultadoIncerto as exc:
            if idempotente:
                return Desfecho.retentar(str(exc), "RESULTADO_INCERTO_IDEMPOTENTE")
            return Desfecho.incerto(str(exc), "RESULTADO_INCERTO")
        except Exception as exc:
            # Exceção não classificada com a chamada já disparada: só é seguro
            # repetir se o tipo for idempotente. Caso contrário, `incerto`.
            logger.exception("Fila: item %s terminou com exceção não classificada", item.id)
            if idempotente:
                return Desfecho.retentar(f"{type(exc).__name__}: {exc}", "ERRO_NAO_CLASSIFICADO")
            return Desfecho.incerto(f"{type(exc).__name__}: {exc}", "ERRO_NAO_CLASSIFICADO")

    def _aplicar(self, item: ItemReivindicado, desfecho: Desfecho, de_status: int,
                 duracao_ms: Optional[int]) -> str:
        catalogo = self._catalogo
        with self._engine.begin() as conn:
            if desfecho.estado is Estado.CONCLUIDO:
                ok = repositorio.concluir(conn, catalogo, item, desfecho.resultado, duracao_ms,
                                          self.ator, de_status=de_status)
                return "concluido" if ok else ""
            if desfecho.estado is Estado.AGUARDANDO_CALLBACK:
                ok = repositorio.marcar_aguardando_callback(conn, catalogo, item, desfecho.resultado,
                                                            self.ator, de_status=de_status)
                return "aguardando_callback" if ok else ""
            if desfecho.estado is Estado.FALHOU:
                ok = repositorio.falhar(conn, catalogo, item, desfecho.erro or "falha no destino",
                                        desfecho.erro_codigo, duracao_ms, self.ator, de_status=de_status)
                return "falhou" if ok else ""
            if desfecho.estado is Estado.INCERTO:
                ok = repositorio.marcar_incerto(conn, catalogo, item, desfecho.erro or "sem confirmação",
                                                desfecho.erro_codigo, self.ator, de_status=de_status)
                return "incerto" if ok else ""
            return repositorio.agendar_retentativa(
                conn, catalogo, item, desfecho.erro or "falha retentável", desfecho.erro_codigo,
                self.atraso(item), self.ator, de_status=de_status,
            )
