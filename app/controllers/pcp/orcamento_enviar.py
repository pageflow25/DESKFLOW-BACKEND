"""`pcp.orcamento.enviar` — POST /api/v1/orcamento. NÃO idempotente.

Substitui o `DespachoOrcamentos` (`servicos/despacho_orcamento.py`) sem mudar a
chamada que chega ao ERP: o corpo sai dos SQLs de `sql/`, escolhidos só pela
origem (o modo de agrupamento saiu em 2026-10-07), com a mesma conferência de
`codigo_externo` (`servicos/pcp/payload.py`). O que muda é de onde vêm os
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
`{orcamento_id, lote_id, requisicao_id, origem, cliente_id, vendedor_id,
forma_pagamento, ids_origem: [int], data_entrega, url_webhook}`. O SQL sai só da
`origem`; `modo_agrupamento`, que itens anteriores a 2026-10-07 ainda trazem, é
ignorado.

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

from ...integracoes.erp import envelope_json, extrair_id_requisicao, sucesso_erp
from ...fila.modelos import Desfecho, ItemReivindicado, Preparo
from ...servicos.pcp.envio import com_modo_assincrono, corpo_para_auditoria
from ...servicos.pcp.payload import PayloadIncompleto, montar_payload_orcamento
from ...utils.conversao import inteiro_positivo
from ..payload_invalido import PayloadInvalido, preparo_invalido
from ..resposta_erp import classificar_falha
from .resposta_assincrona import desfecho_do_ack
from .validators import pcp_validator

TIPO = "pcp.orcamento.enviar"
TIPO_PRECIFICACAO_CUSTO_BUSCAR = "precificacao.custo.buscar"


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
        orcamento, invalido = pcp_validator.orcamento_do_payload(item.payload)
        if invalido is not None:
            return invalido.preparo()

        # Classe do item e url_webhook têm que concordar (antes de qualquer SQL).
        modo, invalido = pcp_validator.modo_da_chamada(item.classe, item.payload)
        if invalido is not None:
            return invalido.preparo()

        try:
            # Roda na transação curta do preparo, como o despacho antigo rodava
            # na sua: é SELECT no SQL da origem, sem I/O externa.
            corpo = montar_payload_orcamento(conn, orcamento, self._erp.identifier)
        except PayloadIncompleto as exc:
            # Cadastro incompleto ou item que o SQL descartou: definitivo.
            return preparo_invalido(str(exc), "ORCAMENTO_INCOMPLETO")

        corpo = com_modo_assincrono(corpo, modo, orcamento.url_webhook)
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

        # Já conferido no preparo: chegar aqui significa classe e URL coerentes.
        modo, _ = pcp_validator.modo_da_chamada(item.classe, item.payload)
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


class HandlerPrecificacaoCustoBuscar(HandlerPcpOrcamentoEnviar):
    """`precificacao.custo.buscar` — o MESMO POST /api/v1/orcamento, para a Calculadora de Orçamento
    do PageFlow buscar o custo dos itens (PageFlow: docs/calculadora-orcamento/14-tipo-custo-sincrono.md).

    Herda tudo: payload, SQL, interpretação e a regra classe × url_webhook. A diferença está no
    catálogo, não no código — o tipo é da classe SÍNCRONA (há alguém esperando na tela), então chega
    sem url_webhook e a resposta do POST é o resultado. O código próprio é o que separa, nos logs e
    na tela da fila, a cotação da Calculadora do envio de lote do PCP.
    """

    tipo = TIPO_PRECIFICACAO_CUSTO_BUSCAR
