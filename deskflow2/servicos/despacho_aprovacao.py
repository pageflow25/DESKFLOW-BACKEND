"""Despacho das aprovações do PCP (POST /api/v1/proposta/aprovar).

Só existe aprovação a enviar se o PageFlow criou a linha pendente (aprovação
automática do lote, ou "Aprovar"/"Reenviar aprovação" na tela). Antes de
aprovar, consulta a proposta (GET /api/v1/proposta) para não aprovar duas
vezes: se os itens já estão confirmados, repassa a consulta como resultado.

A consulta é feita ANTES do claim: se ela falhar (ERP fora), a aprovação fica
pendente para o próximo ciclo em vez de virar erro.
"""

import logging
import time
from typing import Callable

from ..clientes.erp import ErpClient, ErroErp, FalhaLogin, ler_json
from ..repositorios import fila
from ..repositorios.status import carregar_catalogo
from .comum import chamar_erp, com_modo_assincrono, corpo_erro, corpo_para_auditoria
from .repasse import Repassador

logger = logging.getLogger(__name__)

CAMINHO_APROVAR = "/api/v1/proposta/aprovar"
CAMINHO_PROPOSTA = "/api/v1/proposta"
STATUS_ITEM_APROVADO = "confirmada"


def itens_ja_aprovados(consulta: dict, itens_aprovados: list) -> bool:
    """True quando todos os itens a aprovar aparecem como "Confirmada" na
    última proposta do orçamento."""
    ids = {int(item["id"]) for item in itens_aprovados if isinstance(item, dict) and item.get("id") is not None}
    if not ids or not isinstance(consulta, dict):
        return False
    confirmados = set()
    for proposta in consulta.get("data") or []:
        for item in (proposta or {}).get("itens") or []:
            if str(item.get("status", "")).strip().lower() == STATUS_ITEM_APROVADO and item.get("id") is not None:
                confirmados.add(int(item["id"]))
    return ids <= confirmados


class DespachoAprovacoes:
    def __init__(self, engine, erp: ErpClient, repassador: Repassador, settings, dormir: Callable[[float], None] = time.sleep):
        self._engine = engine
        self._erp = erp
        self._repassador = repassador
        self._settings = settings
        self._dormir = dormir

    def executar_ciclo(self) -> int:
        with self._engine.begin() as conn:
            status = carregar_catalogo(conn)
            pendentes = fila.listar_aprovacoes_pendentes(conn, status, self._settings.PCP_ENVIO_LOTE_MAXIMO)
        if not pendentes:
            return 0

        try:
            self._erp.garantir_login()
        except FalhaLogin as exc:
            logger.error("Aprovações: %s pendente(s), mas o login no ERP falhou — nada foi reivindicado: %s", len(pendentes), exc)
            return 0

        despachadas = 0
        for indice, aprovacao_id in enumerate(pendentes):
            if indice > 0:
                self._dormir(self._settings.PCP_PAUSA_ENTRE_ENVIOS_SEGUNDOS)
            if self.despachar(aprovacao_id) not in ("ja_reivindicada", "consulta_indisponivel"):
                despachadas += 1
        return despachadas

    def _consultar_proposta(self, id_orcamento: int) -> dict:
        resposta = self._erp.get(CAMINHO_PROPOSTA, {"id_orcamento": id_orcamento, "apenas_ultima": "true"})
        dados = ler_json(resposta)
        return dados if isinstance(dados, dict) else {"success": False, "message": f"HTTP {resposta.status_code}"}

    def despachar(self, aprovacao_id: int) -> str:
        with self._engine.begin() as conn:
            id_orcamento = fila.id_orcamento_da_aprovacao(conn, aprovacao_id)

        consulta = None
        if id_orcamento:
            try:
                consulta = self._consultar_proposta(id_orcamento)
            except ErroErp as exc:
                logger.warning("Aprovação #%s: consulta da proposta falhou (%s); fica para o próximo ciclo", aprovacao_id, exc)
                return "consulta_indisponivel"

        with self._engine.begin() as conn:
            status = carregar_catalogo(conn)
            aprovacao = fila.reivindicar_aprovacao(conn, status, aprovacao_id, self._settings.PCP_MODO_ENVIO)
        if not aprovacao:
            logger.info("Aprovação #%s já foi reivindicada ou mudou de estado", aprovacao_id)
            return "ja_reivindicada"

        rotulo = f"Aprovação #{aprovacao.id} (orçamento ERP {aprovacao.id_orcamento})"
        if not aprovacao.id_orcamento:
            self._repassador.aprovacao(aprovacao.id, corpo_erro("Orçamento sem id_orcamento: não há o que aprovar"))
            return "sem_orcamento"

        if consulta is not None:
            self._repassador.consulta_previa(aprovacao.id, consulta)
            if itens_ja_aprovados(consulta, aprovacao.itens_aprovados):
                logger.warning("%s: proposta já estava aprovada no ERP; resolvendo com a consulta (sem OP/PV)", rotulo)
                self._repassador.aprovacao(aprovacao.id, consulta)
                return "ja_aprovada"

        corpo = {
            "identifier": self._settings.ERP_IDENTIFIER,
            "data": {
                "id_orcamento": aprovacao.id_orcamento,
                "gerar_op": aprovacao.gerar_op,
                "itens": aprovacao.itens_aprovados,
            },
        }
        corpo = com_modo_assincrono(corpo, aprovacao.modo_envio, aprovacao.url_webhook)
        with self._engine.begin() as conn:
            fila.gravar_payload_enviado(conn, fila.TABELA_APROVACOES, aprovacao.id, corpo_para_auditoria(corpo))

        logger.info("%s: aprovando %s item(ns), gerar_op=%s", rotulo, len(aprovacao.itens_aprovados), aprovacao.gerar_op)
        resultado = chamar_erp(self._erp, CAMINHO_APROVAR, corpo, aprovacao.modo_envio, "data")

        if resultado.id_requisicao:
            with self._engine.begin() as conn:
                status = carregar_catalogo(conn)
                fila.gravar_id_requisicao(conn, status, fila.TABELA_APROVACOES, aprovacao.id, resultado.id_requisicao)
            logger.info("%s: aceita pelo ERP (requisição %s), aguardando o webhook", rotulo, resultado.id_requisicao)
            return "aguardando_webhook"

        repasse = self._repassador.aprovacao(aprovacao.id, resultado.corpo_retorno)
        logger.info("%s: resultado repassado ao PageFlow (%s)", rotulo, repasse.desfecho.value)
        return "repassado"
