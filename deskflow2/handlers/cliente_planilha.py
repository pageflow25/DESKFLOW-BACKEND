"""`cliente.planilha_linha` — uma LINHA da planilha. NÃO idempotente.

A chamada ao ERP é exatamente a de `cliente.criar` e `cliente.atualizar`: muda
a ORIGEM (uma linha de arquivo em vez de um formulário), não o que se manda nem
o que se faz com a resposta. Por isso este handler não reimplementa nada — ele
**delega** aos dois handlers já entregues, escolhendo pelo campo `operacao`.

A alternativa (copiar preparo, interpretação e verificação) criaria uma segunda
implementação da regra mais cara do módulo — a verificação de resultado incerto
— e as duas divergiriam na primeira mudança do ERP. Delegando, a planilha herda
de graça: o envelope `{identifier, data}`, os três baldes de classificação, o
`verificar()` por documento e a assimetria deliberada de `divergencias()`.

`operacao` é EXIGIDA, nunca inferida da presença de `id_cliente`. Inferir
erraria para o lado caro: um `atualizar` que perdeu o id viraria um POST e
duplicaria o cliente no ERP — e `id_cliente` é FK de `escola_unidades` no
PageFlow, então desfazer isso é caro. Payload sem `operacao` reconhecível é
falha DEFINITIVA, sem chamada nenhuma e sem gastar tentativa.

Formato do payload (produzido por `services/Bremen/bremenClientePlanilha.js`):
`{operacao, linha, id_cliente, documento, cliente, extras: {observacao}}`.
Formato do resultado, lido por `services/fila/projetores/cliente.planilha_linha.js`:
`{"id_cliente": <int>}` — o que os dois delegados já devolvem.

`concorrencia_maxima = 1` no registry preserva a garantia do laço sequencial de
hoje; aqui não há nada a fazer por isso, é o motor que a aplica.
"""

from typing import Any, Optional

from ..fila.modelos import Desfecho, Estado, ItemReivindicado, Preparo
from .cliente_atualizar import HandlerClienteAtualizar
from .cliente_criar import HandlerClienteCriar
from .comum import PayloadInvalido

TIPO = "cliente.planilha_linha"

OPERACAO_CRIAR = "criar"
OPERACAO_ATUALIZAR = "atualizar"
OPERACOES = (OPERACAO_CRIAR, OPERACAO_ATUALIZAR)


def operacao_do_payload(payload: Optional[dict]) -> Optional[str]:
    """A operação declarada pelo produtor, ou `None` quando não é uma das duas
    conhecidas. Nunca deduz a partir de `id_cliente` (ver o módulo)."""
    bruto = (payload or {}).get("operacao")
    if not isinstance(bruto, str):
        return None
    operacao = bruto.strip().lower()
    return operacao if operacao in OPERACOES else None


def _linha_de(payload: Optional[dict]) -> Any:
    return (payload or {}).get("linha")


class HandlerClientePlanilhaLinha:
    tipo = TIPO

    def __init__(self, erp, criar=None, atualizar=None):
        # Os delegados são injetáveis para o teste poder afirmar QUE a delegação
        # acontece, sem depender do transporte.
        self._delegados = {
            OPERACAO_CRIAR: criar or HandlerClienteCriar(erp),
            OPERACAO_ATUALIZAR: atualizar or HandlerClienteAtualizar(erp),
        }

    def _delegado(self, item: ItemReivindicado):
        return self._delegados.get(operacao_do_payload(item.payload))

    def _invalido(self, item: ItemReivindicado) -> PayloadInvalido:
        return PayloadInvalido(
            f"cliente.planilha_linha (linha {_linha_de(item.payload) or '?'}) exige "
            f"`operacao` igual a 'criar' ou 'atualizar'; a operação não é deduzida "
            f"do payload para não transformar uma edição em um cadastro duplicado.",
            "PLANILHA_OPERACAO_INVALIDA",
        )

    def _anotar(self, item: ItemReivindicado, desfecho: Optional[Desfecho]) -> Optional[Desfecho]:
        """Acrescenta a origem ao resultado: é o que liga a linha do arquivo ao
        item na timeline quando alguém for entender uma planilha meio aplicada."""
        if desfecho is None or desfecho.estado is not Estado.CONCLUIDO:
            return desfecho
        desfecho.resultado = {
            **(desfecho.resultado or {}),
            "linha": _linha_de(item.payload),
            "operacao": operacao_do_payload(item.payload),
        }
        return desfecho

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        delegado = self._delegado(item)
        if delegado is None:
            invalido = self._invalido(item)
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)

        preparo = delegado.preparar(conn, item)
        if isinstance(preparo.payload_enviado, dict):
            preparo.payload_enviado = {
                **preparo.payload_enviado,
                "linha": _linha_de(item.payload),
                "operacao": operacao_do_payload(item.payload),
            }
        return preparo

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()
        delegado = self._delegado(item)
        if delegado is None:
            return self._invalido(item).desfecho()
        return self._anotar(item, delegado.interpretar(item, bruto))

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Mesma verificação dos dois handlers de cliente: `criar` pergunta se o
        cliente passou a existir, `atualizar` pergunta se o ERP já reflete o que
        foi mandado."""
        delegado = self._delegado(item)
        if delegado is None:
            return None
        return self._anotar(item, delegado.verificar(conn, item))
