"""`pcp.aprovacao.enviar` — POST /api/v1/proposta/aprovar. NÃO idempotente.

Substitui o `DespachoAprovacoes` (`servicos/despacho_aprovacao.py`), preservando
a única proteção que aquele caminho tinha contra o erro mais caro daqui:
**aprovar duas vezes gera OP e PV duplicados na produção.** Por isso a consulta
prévia ao `GET /api/v1/proposta` continua existindo, e continua acontecendo
ANTES do POST — se todos os itens já estão "Confirmada" no ERP, o item fecha com
a consulta como resultado e nenhum POST sai.

Diferenças em relação ao caminho antigo:

- a consulta prévia agora roda DENTRO da chamada do item (`Preparo.chamar`), e
  não antes do claim. O motivo é o contrato do protocolo: `preparar` roda em
  transação curta e não pode fazer I/O externa. O efeito prático é o mesmo, com
  uma garantia a mais — o item já está com lease renovado enquanto ela roda;
- consulta que NÃO pôde ser concluída é `retentar`, nunca `incerto`: o GET não
  tem efeito, então nada pode ter acontecido no ERP e repetir é seguro. Isso
  espelha o `consulta_indisponivel` de antes, que deixava a linha pendente para
  o próximo ciclo;
- nada é gravado em `orcamento_api_aprovacoes`. Resultado e consulta prévia (que
  antes ia ao PageFlow por um endpoint próprio, só para auditoria) saem os dois
  em `fila_processamento.resultado`.

Payload: `{aprovacao_id, orcamento_id, id_orcamento, gerar_op, itens_aprovados,
url_webhook}`.

Resultado: `{id_requisicao, modo_envio, resposta}`, mais `consulta_previa` e
`ja_aprovada` quando a consulta aconteceu.

`id_orcamento` ausente é `PayloadInvalido` — falha DEFINITIVA que não gasta
tentativa. Sem ele não há o que aprovar, e ele não aparece com o tempo: quem o
grava é o retorno do orçamento.
"""

from typing import Any, Optional

from ..clientes.erp import ErroErp, extrair_id_requisicao, sucesso_erp
from ..fila.modelos import Desfecho, ItemReivindicado, Preparo
from ..servicos.comum import com_modo_assincrono, corpo_para_auditoria
from ..servicos.despacho_aprovacao import itens_ja_aprovados
from .comum import (
    PayloadInvalido,
    classificar_falha,
    envelope_json,
    inteiro_positivo,
    preparo_invalido,
)
from .pcp_comum import desfecho_do_ack, modo_envio_de, url_webhook_de

TIPO = "pcp.aprovacao.enviar"


class ConsultaIndisponivel:
    """A consulta prévia não pôde ser concluída, e o POST não saiu.

    Sentinela em vez de exceção porque o motor classificaria a exceção como
    `incerto` (o tipo não é idempotente) — e aqui não há nada de incerto: o GET
    não tem efeito, logo o ERP não foi tocado e repetir é seguro.
    """

    def __init__(self, erro: Exception):
        self.erro = erro


class JaAprovada:
    """Os itens já constavam confirmados no ERP: o POST não saiu."""

    def __init__(self, consulta: dict):
        self.consulta = consulta


class Aprovada:
    """A resposta do POST, com a consulta prévia que a antecedeu (ou `None`)."""

    def __init__(self, resposta, consulta: Optional[dict]):
        self.resposta = resposta
        self.consulta = consulta


def _itens_do_payload(payload: dict) -> Optional[list]:
    itens = payload.get("itens_aprovados")
    if not isinstance(itens, list) or not itens:
        return None
    return [item for item in itens if isinstance(item, dict)] or None


class HandlerPcpAprovacaoEnviar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        payload = item.payload or {}

        if inteiro_positivo(payload.get("aprovacao_id")) is None:
            return preparo_invalido("pcp.aprovacao.enviar exige `aprovacao_id` inteiro e maior que zero.")

        id_orcamento = inteiro_positivo(payload.get("id_orcamento"))
        if id_orcamento is None:
            return preparo_invalido(
                "Orçamento sem id_orcamento: não há o que aprovar no ERP.", "SEM_ID_ORCAMENTO")

        itens = _itens_do_payload(payload)
        if itens is None:
            return preparo_invalido(
                "pcp.aprovacao.enviar exige `itens_aprovados` com pelo menos um item.",
                "SEM_ITENS_APROVADOS",
            )

        corpo = {
            "identifier": self._erp.identifier,
            "data": {
                "id_orcamento": id_orcamento,
                "gerar_op": bool(payload.get("gerar_op")),
                "itens": itens,
            },
        }
        corpo = com_modo_assincrono(corpo, modo_envio_de(payload), url_webhook_de(payload))
        return Preparo(
            payload_enviado=corpo_para_auditoria(corpo),
            chamar=lambda: self._chamar(id_orcamento, itens, corpo),
        )

    def _chamar(self, id_orcamento: int, itens: list, corpo: dict):
        """Consulta prévia e, só se ela não resolver, o POST de aprovação."""
        try:
            resposta = self._erp.consultar_proposta(id_orcamento)
        except ErroErp as exc:
            return ConsultaIndisponivel(exc)
        consulta = envelope_json(resposta)
        if consulta is None:
            consulta = {"success": False, "message": f"HTTP {resposta.status_code}"}

        if itens_ja_aprovados(consulta, itens):
            return JaAprovada(consulta)

        return Aprovada(self._erp.aprovar_proposta(corpo), consulta)

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        modo = modo_envio_de(item.payload)

        if isinstance(bruto, ConsultaIndisponivel):
            return Desfecho.retentar(
                f"Consulta prévia da proposta não concluída, aprovação não enviada: {bruto.erro}",
                "CONSULTA_PREVIA_INDISPONIVEL",
            )

        if isinstance(bruto, JaAprovada):
            # Sem OP/PV novos: a proposta já estava aprovada no ERP. Fecha com a
            # consulta no lugar da resposta, que é o que o caminho antigo
            # repassava ao PageFlow neste caso.
            return Desfecho.concluido({
                "id_requisicao": None,
                "modo_envio": modo,
                "resposta": bruto.consulta,
                "consulta_previa": bruto.consulta,
                "ja_aprovada": True,
            })

        resposta, consulta = bruto.resposta, bruto.consulta
        dados = envelope_json(resposta)
        if resposta.status_code >= 400 or not sucesso_erp(dados):
            return classificar_falha(resposta, dados, mutacao=True)

        # O marcador de resultado final da aprovação é a LISTA em `data` (as
        # propostas aprovadas). Sucesso com `data` que não é lista é ack.
        tem_resultado_final = isinstance((dados or {}).get("data"), list)
        resultado = {
            "modo_envio": modo,
            "resposta": dados,
            "consulta_previa": consulta,
            "ja_aprovada": False,
        }

        aguardando = desfecho_do_ack(dados, modo, tem_resultado_final, resultado)
        if aguardando is not None:
            return aguardando

        return Desfecho.concluido({**resultado, "id_requisicao": extrair_id_requisicao(dados)})

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """SEM verificação automática nesta fase, como em `pcp.orcamento.enviar`.

        Aqui existe a consulta que serviria (`GET /api/v1/proposta`), e ela já é
        usada ANTES do envio. Depois de um resultado incerto ela não basta:
        item "Confirmada" não distingue a aprovação que ESTE item fez da que uma
        tentativa anterior fez, e `gerar_op` pode ter gerado OP sem que a
        resposta chegasse. Resolver isso exige acordo com a Bremen sobre como
        casar `id_requisicao` com a proposta — pendente. Até lá o item incerto
        vai para decisão humana, e o tipo segue com
        `tipo_verificacao_codigo = NULL`.
        """
        return None
