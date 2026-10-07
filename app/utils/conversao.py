"""Conversões e normalizações de valores, usadas por todas as camadas.

`so_digitos` e `inteiro_positivo` estavam no antigo `validadores/comum.py`, e
`remover_nulos` em `servicos/pcp/payload.py`. Nenhuma das três é regra de
validação nem de PCP: são transformações de valor, e por isso moram aqui.
"""

from typing import Any, Optional


def so_digitos(valor: Any) -> str:
    """Forma canônica do documento, a mesma que o PageFlow usa nas chaves de
    bloqueio e de idempotência (`services/Bremen/bremenClienteFilaService.js`)."""
    return "".join(caractere for caractere in str(valor or "") if caractere.isdigit())


def inteiro_positivo(valor: Any) -> Optional[int]:
    """Inteiro > 0, ou `None`. Aceita o número como texto, que é como ele chega
    do JSONB do payload em boa parte dos produtores."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        try:
            numero = int(str(valor).strip())
        except (TypeError, ValueError):
            return None
    return numero if numero > 0 else None


def remover_nulos(valor):
    """O ERP converte `null` em 0 nos campos inteiros: chave sem valor não vai."""
    if isinstance(valor, dict):
        return {chave: remover_nulos(item) for chave, item in valor.items() if item is not None}
    if isinstance(valor, list):
        return [remover_nulos(item) for item in valor]
    return valor
