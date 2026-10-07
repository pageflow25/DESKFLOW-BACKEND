"""Regras de payload dos tres tipos do PCP.

Antes estavam no `preparar()` de cada handler, cada uma com a mensagem escrita
inline. Juntas, ficam comparaveis: da para ver de uma vez o que cada tipo exige,
e qual codigo de erro cada recusa carrega para a tela da fila.

Os tres devolvem `(valor, PayloadInvalido | None)`. O `PayloadInvalido` leva a
mensagem E o codigo, porque as recusas daqui nao sao todas iguais:
`SEM_ID_ORCAMENTO` e `SEM_ITENS_APROVADOS` sao distinguiveis de um payload
generico malformado, e o operador precisa dessa diferenca.

`modo_envio_de`, `url_webhook_de` e `modo_da_chamada` moram aqui, e nao em
`controllers/pcp/resposta_assincrona.py`, porque leem o PAYLOAD (e a classe do item). O que ficou la e
`desfecho_do_ack`, que le a RESPOSTA.
"""

from typing import Any, Optional, Tuple

from ....fila.catalogo import CLASSE_ASSINCRONO, CLASSE_SINCRONO
from ....repositorios.pcp import ORIGEM_ESCOLA, ORIGEM_INTEGRACAO, OrcamentoReivindicado
from ....utils.conversao import inteiro_positivo
from ...payload_invalido import PayloadInvalido

MODO_SINCRONO = "sincrono"
MODO_ASSINCRONO = "assincrono"

ORIGENS = (ORIGEM_ESCOLA, ORIGEM_INTEGRACAO)


def url_webhook_de(payload: Optional[dict]) -> Optional[str]:
    url = (payload or {}).get("url_webhook")
    return url if isinstance(url, str) and url.strip() else None


def modo_envio_de(payload: Optional[dict]) -> str:
    """`assincrono` só quando existe webhook para o ERP devolver o resultado."""
    return MODO_ASSINCRONO if url_webhook_de(payload) else MODO_SINCRONO


def modo_da_chamada(classe: str, payload: Optional[dict]) -> Tuple[Optional[str], Optional[PayloadInvalido]]:
    """O modo da chamada ao ERP, conferido nos DOIS sinais (2026-10-07).

    A classe do tipo (a da tela de configuração da fila) diz o modo; a `url_webhook` do payload tem
    que concordar com ela:

    | classe     | url_webhook | resultado                                  |
    |------------|-------------|--------------------------------------------|
    | sincrono   | ausente     | síncrono                                   |
    | sincrono   | presente    | recusa `WEBHOOK_EM_TIPO_SINCRONO`          |
    | assincrono | presente    | assíncrono                                 |
    | assincrono | ausente     | recusa `ASSINCRONO_SEM_WEBHOOK`            |

    Antes o modo saía só da URL: um produtor que a mandasse (ou esquecesse) por engano trocava o modo
    sem ninguém ver — e um envio assíncrono num tipo síncrono deixa o usuário da tela esperando um
    webhook que pode levar horas. Divergência agora é falha DEFINITIVA, visível na tela da fila, e
    nenhuma chamada sai. Só vale para os tipos que podem usar webhook (os POSTs do PCP); os demais
    tipos assíncronos nunca mandam URL.
    """
    tem_url = url_webhook_de(payload) is not None
    if classe == CLASSE_SINCRONO:
        if tem_url:
            return None, PayloadInvalido(
                "Tipo de classe síncrona recebeu url_webhook: a tela está esperando o resultado na hora, "
                "e o ERP o devolveria pelo webhook. Confira o produtor no PageFlow.",
                "WEBHOOK_EM_TIPO_SINCRONO")
        return MODO_SINCRONO, None
    if classe == CLASSE_ASSINCRONO:
        if not tem_url:
            return None, PayloadInvalido(
                "Tipo de classe assíncrona sem url_webhook: o ERP não teria para onde devolver o resultado. "
                "Confira BACKEND_PUBLIC_BASE_URL no PageFlow.",
                "ASSINCRONO_SEM_WEBHOOK")
        return MODO_ASSINCRONO, None
    return None, PayloadInvalido(f"Classe de fila desconhecida: {classe!r}.", "CLASSE_DESCONHECIDA")


def _data_entrega_valida(valor: Any) -> bool:
    """`DD/MM/YYYY`, que é o formato que os SQLs de orçamento colocam em `obs_producao`.

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
    caminho antigo é o que mantém uma representação só: é ela que os SQLs e
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

    # `modo_agrupamento` NÃO é lido: saiu em 2026-10-07, e a origem escola tem
    # um SQL só (o Agrupado). Item antigo da fila que ainda o carregue passa —
    # recusá-lo seria falha DEFINITIVA para um envio que continua válido.

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
