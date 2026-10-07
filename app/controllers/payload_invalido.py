"""Primitivas de validacao de payload, comuns a todos os dominios.

Estavam em `handlers/comum.py`, junto da leitura de envelope do ERP e da
classificacao de falha. Sao coisas diferentes: aquelas olham a resposta que
chegou, estas olham o payload que entrou. Ver o docstring do pacote.
"""

from typing import Any, Optional

from ..fila.modelos import Desfecho, Preparo


class PayloadInvalido:
    """Devolvida por `preparar()` NO LUGAR da chamada, quando o item não tem o
    mínimo para falar com o ERP.

    Não é exceção de propósito: o motor trata exceção no preparo como
    retentável, e payload inválido não melhora na tentativa seguinte.
    `interpretar()` a reconhece e devolve falha definitiva, sem gastar tentativa
    e sem nenhuma chamada sair.
    """

    def __init__(self, mensagem: str, codigo: str = "PAYLOAD_INVALIDO"):
        self.mensagem = mensagem
        self.codigo = codigo

    def desfecho(self) -> Desfecho:
        return Desfecho.falhou(self.mensagem, self.codigo)

    def preparo(self) -> Preparo:
        """O `Preparo` correspondente, para o handler devolver direto.

        Existe para o handler não precisar repetir `preparo_invalido(x.mensagem,
        x.codigo)` quando já tem o objeto em mão.
        """
        return Preparo(payload_enviado={"erro": self.mensagem}, chamar=lambda: self)


def preparo_invalido(mensagem: str, codigo: str = "PAYLOAD_INVALIDO") -> Preparo:
    """O `Preparo` de um item que não tem o mínimo para falar com o destino.

    Empacota o idioma que os handlers repetiam à mão: NENHUMA chamada sai (o
    `chamar` devolve o próprio `PayloadInvalido`, que o `interpretar` reconhece)
    e o motivo fica em `payload_enviado`, visível na tela da fila.
    """
    return PayloadInvalido(mensagem, codigo).preparo()


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
