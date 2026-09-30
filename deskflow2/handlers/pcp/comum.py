"""Peças comuns aos dois handlers de ESCRITA do PCP (orçamento e aprovação).

Os dois mandam um POST não idempotente ao ERP e os dois podem receber, em vez
do resultado, um *ack* de que a chamada foi aceita e o resultado virá depois
pelo webhook. É essa segunda metade que mora aqui, porque é onde estava a
decisão mais fácil de errar na migração:

**um item aceito em modo assíncrono NÃO está concluído.** Quem o conclui é o
webhook do ERP, que chega no PageFlow — nunca neste worker. O desfecho certo é
`aguardando_callback`: a linha sai do worker (sem lease, sem ocupar vaga) e
fica esperando o retorno, em vez de virar `concluido` com um resultado que
ainda não existe.

A escolha entre síncrono e assíncrono é a MESMA de hoje
(`repositorios/fila.py::_modo_efetivo`): sem `url_webhook` não há para onde o
ERP devolver o resultado, então a chamada vai síncrona e a resposta do próprio
POST é o resultado.
"""

from typing import Optional

from ...integracoes.erp import extrair_id_requisicao
from ...fila.modelos import Desfecho
from ...validadores.pcp import MODO_ASSINCRONO

def desfecho_do_ack(dados: dict, modo: str, tem_resultado_final: bool,
                    resultado: dict) -> Optional[Desfecho]:
    """O desfecho de uma resposta de SUCESSO que ainda não é o resultado.

    `None` quando a resposta JÁ traz o resultado — aí quem decide é o handler,
    que sabe ler o resultado dele. O ERP às vezes responde o resultado completo
    mesmo em chamada assíncrona, e nesse caso não há webhook a esperar.

    Um ack sem `id_requisicao` é INCERTO, não falha: o ERP disse que aceitou, e
    sem o id não há como ligar o webhook que vier a este item. Como os dois
    tipos são NÃO idempotentes, o item para para decisão humana em vez de ser
    reenviado às cegas.
    """
    if modo != MODO_ASSINCRONO or tem_resultado_final:
        return None
    id_requisicao = extrair_id_requisicao(dados)
    if id_requisicao is None:
        return Desfecho.incerto(
            "ERP aceitou a chamada assíncrona sem informar id_requisicao: o webhook que "
            "chegar não poderá ser ligado a este item.",
            "ERP_ACK_SEM_REQUISICAO",
        )
    return Desfecho.aguardando_callback({**resultado, "id_requisicao": id_requisicao})
