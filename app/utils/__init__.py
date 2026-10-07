"""Funções utilitárias puras, sem regra de domínio e sem I/O.

Entra aqui só o que qualquer camada pode usar sem saber de fila, ERP ou PCP.
Regra de negócio vai para `servicos/`; regra de payload, para o
`validators/` de cada módulo em `controllers/`.
"""

from .conversao import inteiro_positivo, remover_nulos, so_digitos

__all__ = ["inteiro_positivo", "remover_nulos", "so_digitos"]
