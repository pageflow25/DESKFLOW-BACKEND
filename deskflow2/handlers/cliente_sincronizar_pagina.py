"""`cliente.sincronizar_pagina` — UMA página do GET /api/v1/cliente. Idempotente.

Uma página, nunca a varredura: é a regra da seção 9 do plano (*um item de fila é
uma chamada ao destino, nunca um algoritmo*). As ~45 páginas de hoje viram 45
itens de 1–3 s, cada um retentável, cancelável e visível sozinho — em vez de um
request HTTP aberto por minutos que, ao cair na página 40, joga fora as 39
anteriores.

Quem enfileira as páginas 2..N é o PageFlow, no projetor
(`services/fila/paginacaoEncadeada.js`), porque só depois da primeira chamada é
que o destino informa quantas páginas existem. Daí a única regra própria deste
handler: **`total_paginas` é obrigatório na página 1**.

Sem ele, o projetor não encadeia nada e a sincronização termina com uma página
espelhada e 44 nunca pedidas — sem erro, sem alerta e com o espelho parecendo
atualizado. Por isso a ausência é falha DEFINITIVA: aparece na tela da fila e no
DLQ, com o motivo escrito. Retentar não ajudaria (a resposta do ERP veio inteira
e simplesmente não traz `metadata`), e por desenho falha definitiva não consome
o orçamento de tentativas.

Formato do payload: `{pagina: <int>}`.
Formato do resultado, lido por `services/fila/projetores/cliente.sincronizar_pagina.js`:
`{"pagina": <int>, "total_paginas": <int|None>, "clientes": [<objeto cru do ERP>]}`.
"""

from typing import Any, Optional

from ..clientes.erp import sucesso_erp
from ..fila.modelos import Desfecho, ItemReivindicado, Preparo
from .comum import (
    PayloadInvalido,
    classificar_falha,
    clientes_do_envelope,
    envelope_json,
    inteiro_positivo,
    total_paginas_de,
)

TIPO = "cliente.sincronizar_pagina"

PRIMEIRA_PAGINA = 1


def pagina_do_payload(payload: Optional[dict]) -> Optional[int]:
    payload = payload or {}
    return inteiro_positivo(payload.get("pagina", payload.get("page")))


class HandlerClienteSincronizarPagina:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        pagina = pagina_do_payload(item.payload)
        if pagina is None:
            invalido = PayloadInvalido(
                "cliente.sincronizar_pagina exige `pagina` inteira e maior que zero.")
            return Preparo(payload_enviado={"erro": invalido.mensagem}, chamar=lambda: invalido)

        return Preparo(
            payload_enviado={"metodo": "GET", "caminho": "/api/v1/cliente", "params": {"page": pagina}},
            chamar=lambda: self._erp.listar_clientes(page=pagina),
        )

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        dados = envelope_json(bruto)
        if bruto.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(bruto, dados, mutacao=False)

        pagina = pagina_do_payload(item.payload)
        total_paginas = total_paginas_de(dados)
        if pagina == PRIMEIRA_PAGINA and total_paginas is None:
            return Desfecho.falhou(
                "ERP respondeu a página 1 sem `metadata.pages`: sem o total de páginas "
                "o PageFlow não enfileira as páginas seguintes e a sincronização "
                "terminaria truncada sem ninguém perceber.",
                "ERP_SEM_TOTAL_PAGINAS",
            )

        clientes = clientes_do_envelope(dados)
        return Desfecho.concluido({
            "pagina": pagina,
            "total_paginas": total_paginas,
            "clientes": clientes,
            "total": len(clientes),
        })

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Leitura idempotente: o motor já pode retentar sozinho, não há o que
        verificar."""
        return None
