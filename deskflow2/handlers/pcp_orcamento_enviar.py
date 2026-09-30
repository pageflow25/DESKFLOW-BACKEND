"""`pcp.orcamento.enviar` — POST /api/v1/orcamento. NÃO idempotente.

Substitui o `DespachoOrcamentos` (`servicos/despacho_orcamento.py`) sem mudar a
chamada que chega ao ERP: o corpo continua saindo dos MESMOS três SQLs, com a
mesma escolha por origem e modo de agrupamento e a mesma conferência de
`codigo_externo` (`servicos/payload.py`). O que muda é de onde vêm os
parâmetros e para onde vai o resultado:

- **antes**: o worker fazia claim em `orcamento_api_orcamentos`, lia
  cliente/vendedor/forma/ids do banco, gravava `payload_enviado`,
  `modo_envio`, `data_envio` e `id_requisicao` na linha de domínio, e repassava
  a resposta ao PageFlow por HTTP;
- **agora**: tudo isso vem no `payload` do item (o produtor do PageFlow monta),
  e NADA é gravado em `orcamento_api_*`. O único destino do resultado é
  `fila_processamento.resultado`, que o projetor do PageFlow lê. A role
  `deskflow_fila` só tem GRANT nas colunas de execução da fila — uma gravação
  de domínio aqui não seria só errada, seria negada pelo banco.

Payload (contrato fixo com o produtor):
`{orcamento_id, lote_id, requisicao_id, origem, modo_agrupamento, cliente_id,
vendedor_id, forma_pagamento, ids_origem: [int], data_entrega, url_webhook}`.

Resultado:
`{id_orcamento, id_requisicao, modo_envio, resposta}` — `id_orcamento` vem
`None` no ack assíncrono, porque nesse caso quem o traz é o webhook.

Falha: cadastro incompleto (unidade/integração sem cliente, vendedor ou forma
de pagamento) e orçamento que o SQL não conseguiu montar são falhas
DEFINITIVAS, como já eram. Não é estado do ERP que muda com o tempo: é cadastro
que alguém precisa corrigir antes de reenviar, e insistir cinco vezes só
adiaria a mensagem.
"""

from typing import Any, Optional

from ..clientes.erp import extrair_id_requisicao, sucesso_erp
from ..fila.modelos import Desfecho, ItemReivindicado, Preparo
from ..repositorios.fila import ORIGEM_ESCOLA, ORIGEM_INTEGRACAO, OrcamentoReivindicado
from ..servicos.comum import com_modo_assincrono, corpo_para_auditoria
from ..servicos.payload import PayloadIncompleto, montar_payload_orcamento
from .comum import (
    PayloadInvalido,
    classificar_falha,
    envelope_json,
    inteiro_positivo,
    preparo_invalido,
)
from .pcp_comum import desfecho_do_ack, modo_envio_de, url_webhook_de

TIPO = "pcp.orcamento.enviar"

ORIGENS = (ORIGEM_ESCOLA, ORIGEM_INTEGRACAO)
MODOS_AGRUPAMENTO = ("unidade", "escola")


def _data_entrega_valida(valor: Any) -> bool:
    """`DD/MM/YYYY`, que é o formato que os três SQLs colocam em `obs_producao`.

    A checagem é de forma, não de calendário: o que se quer evitar é texto
    arbitrário do payload viajando para dentro da observação de produção.
    """
    partes = str(valor).split("/")
    if len(partes) != 3:
        return False
    dia, mes, ano = partes
    return (len(dia), len(mes), len(ano)) == (2, 2, 4) and all(p.isdigit() for p in partes)


def orcamento_do_payload(payload: Optional[dict]):
    """Traduz o payload do item no `OrcamentoReivindicado` que
    `montar_payload_orcamento` já sabe consumir.

    Devolve `(orcamento, None)` ou `(None, mensagem)`. Reaproveitar a dataclass
    do caminho antigo é de propósito: é ela que os três SQLs e as duas
    validações de `servicos/payload.py` enxergam, e uma segunda representação
    dos mesmos campos seria a chance perfeita de os dois caminhos divergirem.
    """
    payload = payload or {}

    orcamento_id = inteiro_positivo(payload.get("orcamento_id"))
    if orcamento_id is None:
        return None, "pcp.orcamento.enviar exige `orcamento_id` inteiro e maior que zero."

    origem = str(payload.get("origem") or "").strip()
    if origem not in ORIGENS:
        return None, f"Origem do orçamento desconhecida: {payload.get('origem')!r} (esperado um de {list(ORIGENS)})."

    modo_agrupamento = payload.get("modo_agrupamento")
    if origem == ORIGEM_ESCOLA and modo_agrupamento not in MODOS_AGRUPAMENTO:
        return None, (
            f"Origem escola exige `modo_agrupamento` em {list(MODOS_AGRUPAMENTO)}; "
            f"veio {modo_agrupamento!r}."
        )

    brutos = payload.get("ids_origem")
    if not isinstance(brutos, list) or not brutos:
        return None, "pcp.orcamento.enviar exige `ids_origem` com pelo menos um id."
    ids = [inteiro_positivo(bruto) for bruto in brutos]
    if any(id_ is None for id_ in ids):
        return None, f"`ids_origem` tem valor que não é id: {brutos!r}."

    data_entrega = payload.get("data_entrega")
    if data_entrega is not None and not _data_entrega_valida(data_entrega):
        return None, f"`data_entrega` precisa vir como DD/MM/YYYY; veio {data_entrega!r}."

    de_integracao = origem == ORIGEM_INTEGRACAO
    return OrcamentoReivindicado(
        id=orcamento_id,
        requisicao_id=inteiro_positivo(payload.get("requisicao_id")),
        lote_id=inteiro_positivo(payload.get("lote_id")),
        modo_envio=modo_envio_de(payload),
        url_webhook=url_webhook_de(payload),
        # O modo só existe na origem escola; na integração a divisão é fixa
        # (1 orçamento por pedido do parceiro) e o SQL é escolhido pela origem.
        modo_agrupamento=None if de_integracao else modo_agrupamento,
        cliente_id=inteiro_positivo(payload.get("cliente_id")),
        vendedor_id=inteiro_positivo(payload.get("vendedor_id")),
        forma_pagamento=inteiro_positivo(payload.get("forma_pagamento")),
        pedido_distribuicao_ids=[] if de_integracao else ids,
        origem=origem,
        integra_pedido_produto_ids=ids if de_integracao else [],
        data_entrega=data_entrega,
    ), None


def id_orcamento_de(dados: Optional[dict]) -> Optional[int]:
    interno = (dados or {}).get("data")
    if not isinstance(interno, dict):
        return None
    return inteiro_positivo(interno.get("id_orcamento"))


class HandlerPcpOrcamentoEnviar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        orcamento, erro = orcamento_do_payload(item.payload)
        if orcamento is None:
            return preparo_invalido(erro)

        try:
            # Roda na transação curta do preparo, como o despacho antigo rodava
            # na sua: é SELECT nos três SQLs, sem I/O externa.
            corpo = montar_payload_orcamento(conn, orcamento, self._erp.identifier)
        except PayloadIncompleto as exc:
            # Cadastro incompleto ou item que o SQL descartou: definitivo.
            return preparo_invalido(str(exc), "ORCAMENTO_INCOMPLETO")

        corpo = com_modo_assincrono(corpo, orcamento.modo_envio, orcamento.url_webhook)
        return Preparo(
            # `corpo_para_auditoria` tira a url_webhook, que carrega o token do
            # webhook e apareceria na tela da fila.
            payload_enviado=corpo_para_auditoria(corpo),
            chamar=lambda: self._erp.enviar_orcamento(corpo),
        )

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        dados = envelope_json(bruto)
        if bruto.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(bruto, dados, mutacao=True)

        modo = modo_envio_de(item.payload)
        id_orcamento = id_orcamento_de(dados)
        resultado = {"id_orcamento": id_orcamento, "modo_envio": modo, "resposta": dados}

        aguardando = desfecho_do_ack(dados, modo, id_orcamento is not None, resultado)
        if aguardando is not None:
            return aguardando

        if id_orcamento is None:
            # Sucesso síncrono sem id: o orçamento provavelmente existe no ERP e
            # o identificador se perdeu. Incerto, e como o tipo NÃO é
            # idempotente o motor não reenvia sozinho.
            return Desfecho.incerto(
                "ERP respondeu sucesso sem id_orcamento.", "ERP_SUCESSO_SEM_ID")

        return Desfecho.concluido({**resultado, "id_requisicao": extrair_id_requisicao(dados)})

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """SEM verificação, de propósito — e é por isso que o tipo nasce com
        `tipo_verificacao_codigo = NULL` no catálogo.

        A verificação natural seria consultar o orçamento no ERP pelo
        `codigo_externo` (que carrega os ids de origem), mas AINDA NÃO ESTÁ
        CONFIRMADO com a Bremen que a API permite essa consulta. Inventar um
        caminho de API seria pior que a espera: um verificador que erra a
        pergunta responde "não existe" para algo que existe, e aí o item é
        reenviado e o orçamento duplica no ERP.

        Até a confirmação, resultado incerto vai para decisão humana — o mesmo
        que acontecia antes desta fase, só que agora visível na tela da fila
        com o `payload_enviado` ao lado. Mesma situação de `vendedor.listar_pagina`.
        """
        return None
