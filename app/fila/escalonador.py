"""Escalonamento: dois pools, dois despachantes, reaper e heartbeat.

Threads, não asyncio e não processos: todo o código existente é síncrono e a
carga é 99 % espera de I/O.

**Pool síncrono = 1.** É a regra do usuário: um lançamento síncrono roda de cada
vez, e o segundo e os seguintes esperam na fila, em ordem. Não é um limite de
enfileiramento — entra quanto quiser; o que é limitado é quanto roda junto.

**Pool assíncrono = 4**, independente do síncrono. Os dois predicados de claim
não se cruzam (`classe` diferente), então os pools não disputam nem as mesmas
páginas de índice nem as mesmas conexões ao destino (a reserva de classe do
semáforo do `ErpClient` cuida da segunda metade).

Nada de `LISTEN/NOTIFY` e nada de lock de sessão: o banco responde pelo pooler
de transação do Supabase (6543), onde nenhum dos dois funciona. O pickup é por
poll curto (1 s no síncrono, 5 s no assíncrono) e toda coordenação é
`pg_advisory_xact_lock`.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import repositorio
from .catalogo import CLASSE_ASSINCRONO, CLASSE_SINCRONO, Catalogo
from .motor import Motor

logger = logging.getLogger(__name__)


class Despachante:
    """Alimenta UM pool: reivindica só o que cabe nos slots livres e entrega
    cada item a uma thread. Nunca claim além da capacidade — item reivindicado
    e parado na fila do executor é lease queimado à toa."""

    def __init__(self, engine, catalogo: Catalogo, motor: Motor, settings, classe: str,
                 capacidade: int, executor: ThreadPoolExecutor, worker_id: str, destino: str):
        self._engine = engine
        self._catalogo = catalogo
        self._motor = motor
        self._settings = settings
        self._classe = classe
        self._capacidade = capacidade
        self._executor = executor
        self._worker_id = worker_id
        self._destino = destino
        self._trava = threading.Lock()
        self._em_voo = 0

    @property
    def em_voo(self) -> int:
        with self._trava:
            return self._em_voo

    @property
    def capacidade(self) -> int:
        return self._capacidade

    def tick(self) -> int:
        livres = self._capacidade - self.em_voo
        lote = min(livres, max(1, self._settings.FILA_LOTE_CLAIM))
        if lote <= 0:
            return 0

        with self._engine.begin() as conn:
            itens = repositorio.reivindicar(
                conn, self._catalogo, self._classe, self._destino, self._worker_id,
                lote, self._settings.FILA_LEASE_MARGEM_SEGUNDOS,
                janela=lote * max(1, self._settings.FILA_JANELA_CLAIM_MULTIPLICADOR),
            )
        if not itens:
            return 0

        logger.info("Fila %s: %s item(ns) reivindicado(s)", self._classe, len(itens))
        for item in itens:
            with self._trava:
                self._em_voo += 1
            self._executor.submit(self._executar, item)
        return len(itens)

    def _executar(self, item) -> None:
        try:
            self._motor.processar(item)
        except Exception:
            # O motor já trata o que sabe tratar; aqui é só para nenhuma thread
            # do pool morrer em silêncio.
            logger.exception("Fila %s: item %s terminou com erro não tratado", self._classe, item.id)
        finally:
            with self._trava:
                self._em_voo -= 1


class Escalonador:
    def __init__(self, engine, catalogo: Catalogo, registry, settings, worker_id: Optional[str] = None):
        from .. import __version__

        self._engine = engine
        self._catalogo = catalogo
        self._settings = settings
        self._pid = os.getpid()
        self.worker_id = worker_id or settings.FILA_WORKER_ID or repositorio.identificador_padrao(self._pid)
        self._versao = __version__
        self._motor = Motor(engine, catalogo, registry, settings, self.worker_id)

        self._pool_sincrono = ThreadPoolExecutor(
            max_workers=max(1, settings.FILA_POOL_SINCRONO), thread_name_prefix="fila-sinc")
        self._pool_assincrono = ThreadPoolExecutor(
            max_workers=max(1, settings.FILA_POOL_ASSINCRONO), thread_name_prefix="fila-assinc")

        self.sincrono = Despachante(
            engine, catalogo, self._motor, settings, CLASSE_SINCRONO,
            max(1, settings.FILA_POOL_SINCRONO), self._pool_sincrono, self.worker_id, settings.FILA_DESTINO)
        self.assincrono = Despachante(
            engine, catalogo, self._motor, settings, CLASSE_ASSINCRONO,
            max(1, settings.FILA_POOL_ASSINCRONO), self._pool_assincrono, self.worker_id, settings.FILA_DESTINO)

    # --- Ciclos do agendador -------------------------------------------------

    def ciclo_sincrono(self) -> int:
        return self.sincrono.tick()

    def ciclo_assincrono(self) -> int:
        return self.assincrono.tick()

    def ciclo_reaper(self) -> int:
        with self._engine.begin() as conn:
            resumo = repositorio.reaper(
                conn, self._catalogo, self._motor.ator,
                self._settings.FILA_ENVELHECIMENTO_MINUTOS,
                self._settings.FILA_ENVELHECIMENTO_PASSO,
                self._settings.FILA_ENVELHECIMENTO_TETO,
            )
        if resumo["ignorado"]:
            return 0
        total = resumo["devolvidos"] + resumo["incertos"] + resumo["dlq"] + resumo["envelhecidos"]
        if total:
            logger.info(
                "Fila reaper: %s devolvido(s), %s incerto(s), %s para DLQ, %s envelhecido(s)",
                resumo["devolvidos"], resumo["incertos"], resumo["dlq"], resumo["envelhecidos"],
            )
        return total

    def ciclo_heartbeat(self) -> int:
        em_execucao = self.sincrono.em_voo + self.assincrono.em_voo
        with self._engine.begin() as conn:
            repositorio.heartbeat(
                conn,
                identificador=self.worker_id,
                classes=[CLASSE_SINCRONO, CLASSE_ASSINCRONO],
                versao=self._versao,
                pid=self._pid,
                capacidade=self.sincrono.capacidade + self.assincrono.capacidade,
                itens_em_execucao=em_execucao,
                detalhe={
                    "destino": self._settings.FILA_DESTINO,
                    "pool_sincrono": self.sincrono.capacidade,
                    "pool_assincrono": self.assincrono.capacidade,
                    "em_voo_sincrono": self.sincrono.em_voo,
                    "em_voo_assincrono": self.assincrono.em_voo,
                },
            )
        return 0

    def encerrar(self) -> None:
        self._pool_sincrono.shutdown(wait=True, cancel_futures=True)
        self._pool_assincrono.shutdown(wait=True, cancel_futures=True)
