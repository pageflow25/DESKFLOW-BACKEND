"""Tipos de dado que atravessam as camadas da fila.

O motor só entende `Preparo` e `Desfecho`; o que está dentro deles é assunto do
handler. É isso que mantém o motor sem conhecer nenhum destino.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional
from uuid import UUID


class Estado(str, Enum):
    """Desfecho possível de uma execução, do ponto de vista do motor.

    Os três baldes da seção 5 do plano, mais os dois estados de espera:

    - `CONCLUIDO`  — o destino processou e aceitou;
    - `FALHOU`     — o destino processou e recusou (erro definitivo). NÃO gasta
                     tentativa: o CNPJ inválido não fica melhor na terceira vez;
    - `RETENTAR`   — certeza de que o destino NÃO processou (503, sem conexão);
    - `INCERTO`    — pode ter processado e a resposta não chegou;
    - `AGUARDANDO_CALLBACK` — aceito, o resultado vem por webhook.
    """

    CONCLUIDO = "concluido"
    FALHOU = "falhou"
    RETENTAR = "retentar"
    INCERTO = "incerto"
    AGUARDANDO_CALLBACK = "aguardando_callback"


@dataclass
class ItemReivindicado:
    """Uma linha de `fila_processamento` com o claim já commitado."""

    id: int
    tipo_codigo: str
    classe: str
    destino: str
    prioridade: int
    origem: str
    status_id: int
    grupo_id: Optional[int]
    ordem_no_grupo: Optional[int]
    correlation_id: UUID
    chave_bloqueio: Optional[str]
    chave_idempotencia: Optional[str]
    payload: dict
    payload_enviado: Optional[dict]
    tentativa: int
    max_tentativas: int
    cancelamento_solicitado: bool
    solicitante_usuario_id: Optional[int]
    lease_expira_em: Optional[datetime]
    criado_em: Optional[datetime]

    @property
    def ultima_tentativa(self) -> bool:
        return self.tentativa >= self.max_tentativas


@dataclass
class Preparo:
    """O que o handler devolve antes da chamada ao destino.

    - `payload_enviado`: corpo real montado pelo handler, gravado na linha
      ANTES da chamada (auditoria: se a resposta se perder, ainda se sabe o que
      foi mandado). Já deve vir sem segredo — o motor não redige nada.
    - `chamar`: função sem argumentos que faz a I/O. O motor a executa FORA de
      qualquer transação, com o lease renovado, e entrega o retorno cru ao
      `interpretar`.
    """

    payload_enviado: Optional[dict] = None
    chamar: Optional[Callable[[], Any]] = None


@dataclass
class Desfecho:
    estado: Estado
    resultado: Optional[dict] = None
    erro: Optional[str] = None
    erro_codigo: Optional[str] = None
    detalhe: dict = field(default_factory=dict)

    @classmethod
    def concluido(cls, resultado: Optional[dict] = None) -> "Desfecho":
        return cls(Estado.CONCLUIDO, resultado=resultado)

    @classmethod
    def falhou(cls, erro: str, erro_codigo: Optional[str] = None) -> "Desfecho":
        return cls(Estado.FALHOU, erro=erro, erro_codigo=erro_codigo)

    @classmethod
    def retentar(cls, erro: str, erro_codigo: Optional[str] = None) -> "Desfecho":
        return cls(Estado.RETENTAR, erro=erro, erro_codigo=erro_codigo)

    @classmethod
    def incerto(cls, erro: str, erro_codigo: Optional[str] = None) -> "Desfecho":
        return cls(Estado.INCERTO, erro=erro, erro_codigo=erro_codigo)

    @classmethod
    def aguardando_callback(cls, resultado: Optional[dict] = None) -> "Desfecho":
        return cls(Estado.AGUARDANDO_CALLBACK, resultado=resultado)
