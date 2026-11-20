"""`PayloadInvalido`: a recusa que os validators de cada modulo devolvem.

Nao e um validador: e o TIPO da recusa, o mesmo para todos os controllers, e por
isso mora em `controllers/` e nao dentro do `validators/` de um modulo. As regras
ficam em `controllers/<modulo>/validators/`.

Todo validador devolve `PayloadInvalido` ou `None`, nunca levanta excecao. A
diferenca importa: excecao no `preparar()` e RETENTAVEL por construcao no motor,
e payload invalido nao melhora na terceira tentativa — ele e falha definitiva e
nao deve gastar o orcamento de tentativas do item.

A fronteira com `controllers/resposta_erp.py` e a direcao do dado: validator
olha o payload que ENTROU, antes de qualquer chamada sair; `resposta_erp.py`
olha a resposta que CHEGOU.
"""

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
