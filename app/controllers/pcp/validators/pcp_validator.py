"""Regras de payload dos tres tipos do PCP.

Antes estavam no `preparar()` de cada handler, cada uma com a mensagem escrita
inline. Juntas, ficam comparaveis: da para ver de uma vez o que cada tipo exige,
e qual codigo de erro cada recusa carrega para a tela da fila.

Os tres devolvem `(valor, PayloadInvalido | None)`. O `PayloadInvalido` leva a
mensagem E o codigo, porque as recusas daqui nao sao todas iguais:
`SEM_ID_ORCAMENTO` e `SEM_ITENS_APROVADOS` sao distinguiveis de um payload
generico malformado, e o operador precisa dessa diferenca.

`modo_envio_de` e `url_webhook_de` moram aqui, e nao em
`controllers/pcp/resposta_assincrona.py`, porque leem o PAYLOAD. O que ficou la e `desfecho_do_ack`, que le a RESPOSTA.
"""

from typing import Any, Optional, Tuple

from ....repositorios.pcp import ORIGEM_ESCOLA, ORIGEM_INTEGRACAO, OrcamentoReivindicado
from ....utils.conversao import inteiro_positivo
from ...payload_invalido import PayloadInvalido

MODO_SINCRONO = "sincrono"
MODO_ASSINCRONO = "assincrono"

ORIGENS = (ORIGEM_ESCOLA, ORIGEM_INTEGRACAO)
MODOS_AGRUPAMENTO = ("unidade", "escola")


def url_webhook_de(payload: Optional[dict]) -> Optional[str]:
    url = (payload or {}).get("url_webhook")
    return url if isinstance(url, str) and url.strip() else None


def modo_envio_de(payload: Optional[dict]) -> str:
    """`assincrono` só quando existe webhook para o ERP devolver o resultado."""
    return MODO_ASSINCRONO if url_webhook_de(payload) else MODO_SINCRONO


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


def orcamento_do_payload(
    payload: Optional[dict],
) -> Tuple[Optional[OrcamentoReivindicado], Optional[PayloadInvalido]]:
    """Traduz o payload do item no `OrcamentoReivindicado` que
    `montar_payload_orcamento` já sabe consumir.

    Valida E constrói no mesmo lugar, de propósito. Reaproveitar a dataclass do
    caminho antigo é o que mantém uma representação só: é ela que os três SQLs e
    as duas validações de `servicos/pcp/payload.py` enxergam, e uma segunda cópia dos
    mesmos campos seria a chance perfeita de os dois caminhos divergirem.
    """
    payload = payload or {}

    orcamento_id = inteiro_positivo(payload.get("orcamento_id"))
    if orcamento_id is None:
        return None, PayloadInvalido(
            "pcp.orcamento.enviar exige `orcamento_id` inteiro e maior que zero.")

    origem = str(payload.get("origem") or "").strip()
    if origem not in ORIGENS:
        return None, PayloadInvalido(
            f"Origem do orçamento desconhecida: {payload.get('origem')!r} "
            f"(esperado um de {list(ORIGENS)}).")

    modo_agrupamento = payload.get("modo_agrupamento")
    if origem == ORIGEM_ESCOLA and modo_agrupamento not in MODOS_AGRUPAMENTO:
        return None, PayloadInvalido(
            f"Origem escola exige `modo_agrupamento` em {list(MODOS_AGRUPAMENTO)}; "
            f"veio {modo_agrupamento!r}.")

    brutos = payload.get("ids_origem")
    if not isinstance(brutos, list) or not brutos:
        return None, PayloadInvalido(
            "pcp.orcamento.enviar exige `ids_origem` com pelo menos um id.")
    ids = [inteiro_positivo(bruto) for bruto in brutos]
    if any(id_ is None for id_ in ids):
        return None, PayloadInvalido(f"`ids_origem` tem valor que não é id: {brutos!r}.")

    data_entrega = payload.get("data_entrega")
    if data_entrega is not None and not _data_entrega_valida(data_entrega):
        return None, PayloadInvalido(
            f"`data_entrega` precisa vir como DD/MM/YYYY; veio {data_entrega!r}.")

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


def aprovacao_do_payload(
    payload: Optional[dict],
) -> Tuple[Optional[int], Optional[PayloadInvalido]]:
    """O `aprovacao_id` de `pcp.aprovacao.enviar`.

    É só dele que o handler precisa: o corpo da aprovação sai de
    `sql/aprovacao.sql`, que lê o orçamento, o `id_orcamento`, o `gerar_op` e
    os itens a partir da aprovação. `id_orcamento` e `itens_aprovados` ainda
    vêm no payload (o PageFlow os manda para a tela da fila), mas não são lidos
    — quem é conferido é o resultado do SQL, em `aprovacao_dos_dados`.
    """
    aprovacao_id = inteiro_positivo((payload or {}).get("aprovacao_id"))
    if aprovacao_id is None:
        return None, PayloadInvalido(
            "pcp.aprovacao.enviar exige `aprovacao_id` inteiro e maior que zero.")
    return aprovacao_id, None


def aprovacao_dos_dados(
    dados: Optional[dict], aprovacao_id: int,
) -> Tuple[Optional[dict], Optional[PayloadInvalido]]:
    """Confere o `data` montado por `sql/aprovacao.sql`.

    Devolve `{id_orcamento, itens, gerar_op}`. As três recusas são definitivas:
    aprovação inexistente, orçamento sem `id_orcamento` (quem o grava é o
    retorno do orçamento, não aparece com o tempo) e nenhum item a aprovar.
    """
    if not isinstance(dados, dict):
        return None, PayloadInvalido(
            f"Aprovação {aprovacao_id} não encontrada no banco.", "APROVACAO_NAO_ENCONTRADA")

    id_orcamento = inteiro_positivo(dados.get("id_orcamento"))
    if id_orcamento is None:
        return None, PayloadInvalido(
            "Orçamento sem id_orcamento: não há o que aprovar no ERP.", "SEM_ID_ORCAMENTO")

    itens = dados.get("itens")
    itens = [item for item in itens if isinstance(item, dict)] if isinstance(itens, list) else []
    if not itens:
        return None, PayloadInvalido(
            "Nenhum item a aprovar: o retorno do orçamento não trouxe itens do ERP.",
            "SEM_ITENS_APROVADOS",
        )

    return {
        "id_orcamento": id_orcamento,
        "itens": itens,
        "gerar_op": bool(dados.get("gerar_op")),
    }, None


def download_do_payload(
    payload: Optional[dict],
) -> Tuple[Optional[int], Optional[PayloadInvalido]]:
    """O `aprovacao_id` de `pcp.download_arquivos`.

    É o único dos três que não manda nada ao ERP por HTTP — baixa arquivos da
    pasta da OP —, e por isso é também o único idempotente no registry. Mesmo
    assim o payload precisa do id, porque é dele que sai a lista de arquivos.
    """
    aprovacao_id = inteiro_positivo((payload or {}).get("aprovacao_id"))
    if aprovacao_id is None:
        return None, PayloadInvalido(
            "pcp.download_arquivos exige `aprovacao_id` inteiro e maior que zero.")
    return aprovacao_id, None
