"""Despacho dos orçamentos do PCP (POST /api/v1/orcamento).

Para cada orcamento_envio_orcamentos em `pendente_envio`:
1. claim CAS no banco (e o lote vai para `em_processamento`);
2. monta o corpo com o SQL do modo do lote e grava `payload_enviado`;
3. chama o ERP:
   - assíncrono: grava o `id_requisicao` do ack e para — o ERP entrega o
     resultado no webhook do PageFlow;
   - síncrono: repassa a resposta ao PageFlow como veio;
4. qualquer falha antes de ter resposta vira `{success:false}` no PageFlow, e
   o usuário reenvia pela tela.
"""

import logging
import time
from typing import Callable

from ..clientes.erp import ErpClient, FalhaLogin
from ..repositorios import fila
from ..repositorios.status import carregar_catalogo
from .comum import chamar_erp, com_modo_assincrono, corpo_erro, corpo_para_auditoria
from .payload import PayloadIncompleto, montar_payload_orcamento
from .repasse import Repassador

logger = logging.getLogger(__name__)

CAMINHO_ORCAMENTO = "/api/v1/orcamento"


class DespachoOrcamentos:
    def __init__(self, engine, erp: ErpClient, repassador: Repassador, settings, dormir: Callable[[float], None] = time.sleep):
        self._engine = engine
        self._erp = erp
        self._repassador = repassador
        self._settings = settings
        self._dormir = dormir

    def executar_ciclo(self) -> int:
        with self._engine.begin() as conn:
            status = carregar_catalogo(conn)
            pendentes = fila.listar_orcamentos_pendentes(conn, status, self._settings.PCP_ENVIO_LOTE_MAXIMO)
        if not pendentes:
            return 0

        try:
            self._erp.garantir_login()
        except FalhaLogin as exc:
            logger.error("Orçamentos: %s pendente(s), mas o login no ERP falhou — nada foi reivindicado: %s", len(pendentes), exc)
            return 0

        despachados = 0
        for indice, orcamento_id in enumerate(pendentes):
            if indice > 0:
                self._dormir(self._settings.PCP_PAUSA_ENTRE_ENVIOS_SEGUNDOS)
            if self.despachar(orcamento_id) != "ja_reivindicado":
                despachados += 1
        return despachados

    def despachar(self, orcamento_id: int) -> str:
        with self._engine.begin() as conn:
            status = carregar_catalogo(conn)
            orcamento = fila.reivindicar_orcamento(conn, status, orcamento_id, self._settings.PCP_MODO_ENVIO)
        if not orcamento:
            logger.info("Orçamento #%s já foi reivindicado ou mudou de estado", orcamento_id)
            return "ja_reivindicado"

        rotulo = f"Orçamento #{orcamento.id} (lote {orcamento.lote_id}, {len(orcamento.pedido_distribuicao_ids)} pedido(s))"
        try:
            with self._engine.begin() as conn:
                corpo = montar_payload_orcamento(conn, orcamento, self._settings.ERP_IDENTIFIER)
        except PayloadIncompleto as exc:
            logger.warning("%s: %s", rotulo, exc)
            self._repassador.orcamento(orcamento.id, corpo_erro(str(exc)))
            return "payload_incompleto"

        corpo = com_modo_assincrono(corpo, orcamento.modo_envio, orcamento.url_webhook)
        with self._engine.begin() as conn:
            fila.gravar_payload_enviado(conn, fila.TABELA_ORCAMENTOS, orcamento.id, corpo_para_auditoria(corpo))

        logger.info("%s: enviando ao ERP (%s, %s item(ns))", rotulo, orcamento.modo_envio, len(corpo["data"]["itens"]))
        resultado = chamar_erp(self._erp, CAMINHO_ORCAMENTO, corpo, orcamento.modo_envio, "id_orcamento")

        if resultado.id_requisicao:
            with self._engine.begin() as conn:
                status = carregar_catalogo(conn)
                fila.gravar_id_requisicao(conn, status, fila.TABELA_ORCAMENTOS, orcamento.id, resultado.id_requisicao)
            logger.info("%s: aceito pelo ERP (requisição %s), aguardando o webhook", rotulo, resultado.id_requisicao)
            return "aguardando_webhook"

        repasse = self._repassador.orcamento(orcamento.id, resultado.corpo_retorno)
        logger.info("%s: resultado repassado ao PageFlow (%s)", rotulo, repasse.desfecho.value)
        return "repassado"
