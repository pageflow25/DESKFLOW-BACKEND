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
url_webhook}`. Só `aprovacao_id` e `url_webhook` são lidos: o `data` do POST
(`id_orcamento`, `gerar_op`, `itens` com datas e entregas) sai de
`sql/aprovacao.sql`, a partir da aprovação — para ser ajustado ali, junto dos
SQLs de orçamento, em vez de vir pronto do PageFlow.

Resultado: `{id_requisicao, modo_envio, resposta}`, mais `consulta_previa` e
`ja_aprovada` quando a consulta aconteceu.

`id_orcamento` ausente (no orçamento lido pelo SQL) é `PayloadInvalido` — falha
DEFINITIVA que não gasta tentativa. Sem ele não há o que aprovar, e ele não
aparece com o tempo: quem o grava é o retorno do orçamento.
"""

from typing import Any, Optional

from ...integracoes.erp import ErroErp, extrair_id_requisicao, sucesso_erp
from ...fila.modelos import Desfecho, ItemReivindicado, Preparo
from ...servicos.pcp.aprovacao import montar_dados_aprovacao
from ...servicos.pcp.comum import com_modo_assincrono, corpo_para_auditoria
from ...validadores import pcp as validadores
from ...validadores.comum import PayloadInvalido
from ..comum import classificar_falha, envelope_json
from .comum import desfecho_do_ack

TIPO = "pcp.aprovacao.enviar"

STATUS_ITEM_APROVADO = "confirmada"


def itens_ja_aprovados(consulta: dict, itens_aprovados: list) -> bool:
    """True quando todos os itens a aprovar aparecem como "Confirmada" na
    última proposta do orçamento.

    Vinha de `servicos/despacho_aprovacao.py` e foi trazida para cá quando o
    despacho antigo saiu: é a única proteção contra aprovar duas vezes (OP e PV
    duplicados na produção), e este handler é seu único chamador.

    `data` só é lista de propostas no sucesso. Quando o orçamento não tem
    proposta, o ERP responde HTTP 400 com `data` OBJETO
    (`{"error": "Proposta não encontrada"}`) — percorrer isso como lista dava
    `'str' object has no attribute 'get'` e o item ia para `incerto` sem o POST
    ter saído. Formato inesperado conta como "nada confirmado": o POST segue e
    a resposta dele é que decide (o handler já classifica a recusa).
    """
    ids = {int(item["id"]) for item in itens_aprovados if isinstance(item, dict) and item.get("id") is not None}
    if not ids or not isinstance(consulta, dict):
        return False
    propostas = consulta.get("data")
    if isinstance(propostas, dict):
        propostas = [propostas]
    if not isinstance(propostas, list):
        return False
    confirmados = set()
    for proposta in propostas:
        itens = proposta.get("itens") if isinstance(proposta, dict) else None
        for item in itens if isinstance(itens, list) else []:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            if str(item.get("status", "")).strip().lower() == STATUS_ITEM_APROVADO:
                confirmados.add(int(item["id"]))
    return ids <= confirmados


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


class HandlerPcpAprovacaoEnviar:
    tipo = TIPO

    def __init__(self, erp):
        self._erp = erp

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        payload = item.payload or {}

        aprovacao_id, invalido = validadores.aprovacao_do_payload(payload)
        if invalido is not None:
            return invalido.preparo()

        # SELECT em `sql/aprovacao.sql`, na transação curta do preparo — sem
        # I/O externa, como o SQL do orçamento.
        campos, invalido = validadores.aprovacao_dos_dados(
            montar_dados_aprovacao(conn, aprovacao_id), aprovacao_id)
        if invalido is not None:
            return invalido.preparo()
        id_orcamento, itens = campos["id_orcamento"], campos["itens"]

        corpo = {
            "identifier": self._erp.identifier,
            "data": {
                "id_orcamento": id_orcamento,
                "gerar_op": campos["gerar_op"],
                "itens": itens,
            },
        }
        corpo = com_modo_assincrono(
            corpo, validadores.modo_envio_de(payload), validadores.url_webhook_de(payload))
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

        modo = validadores.modo_envio_de(item.payload)

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
