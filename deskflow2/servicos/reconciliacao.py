"""Reconciliação: nada fica preso em `aguardando_retorno`.

A cada ciclo:
1. reenvia ao PageFlow os resultados guardados localmente (PageFlow estava
   fora ou recusou a chave);
2. para orçamentos/aprovações esperando retorno há mais de
   PCP_RECONCILIACAO_MINUTOS:
   - com `id_requisicao` (assíncrono): consulta GET /api/v1/requisicao?id=;
     se o ERP já processou, repassa o `payload` (vem como string JSON) ao
     PageFlow — que é idempotente, então chegar junto com o webhook não
     duplica nada;
   - passado PCP_RECONCILIACAO_LIMITE_HORAS (o ERP tenta o webhook 5x, de
     hora em hora), com ou sem `id_requisicao`: repassa `{success:false}`
     para liberar o reenvio na tela, avisando que o ERP pode ter processado.
Decisão do usuário (2026-09-17): quem destrava linha presa é o consumidor, a
tela só mostra "aguardando há X".
"""

import json
import logging

from ..clientes.erp import ErpClient, ErroErp, ler_json
from ..repositorios import fila
from ..repositorios.status import carregar_catalogo
from .comum import corpo_erro
from .repasse import Repassador

logger = logging.getLogger(__name__)

CAMINHO_REQUISICAO = "/api/v1/requisicao"
TIPO_POR_TABELA = {fila.TABELA_ORCAMENTOS: "orcamentos", fila.TABELA_APROVACOES: "aprovacoes"}


def extrair_payload_requisicao(consulta) -> dict | None:
    """`data.payload` do GET /requisicao é o resultado final, como string JSON."""
    if not isinstance(consulta, dict):
        return None
    dados = consulta.get("data")
    if isinstance(dados, list):
        dados = dados[0] if dados else None
    if not isinstance(dados, dict):
        return None
    payload = dados.get("payload")
    if isinstance(payload, str):
        try:
            # strict=False: o ERP grava o payload com quebras de linha cruas
            # dentro dos textos (descrição do item), o que o modo estrito recusa.
            payload = json.loads(payload, strict=False)
        except ValueError:
            logger.warning("Reconciliação: payload da requisição %s ilegível", dados.get("id_requisicao"))
            return None
    return payload if isinstance(payload, dict) else None


def _situacao(consulta) -> str:
    dados = consulta.get("data") if isinstance(consulta, dict) else None
    if isinstance(dados, list):
        dados = dados[0] if dados else None
    if not isinstance(dados, dict):
        return "resposta sem dados"
    return f"status_processamento={dados.get('status_processamento')!r}"


class Reconciliacao:
    def __init__(self, engine, erp: ErpClient, repassador: Repassador, settings):
        self._engine = engine
        self._erp = erp
        self._repassador = repassador
        self._settings = settings

    def executar_ciclo(self) -> int:
        entregues = self._repassador.reenviar_pendentes()
        if entregues:
            logger.info("Reconciliação: %s repasse(s) guardado(s) entregue(s) ao PageFlow", entregues)

        resolvidas = 0
        for tabela, tipo in TIPO_POR_TABELA.items():
            with self._engine.begin() as conn:
                status = carregar_catalogo(conn)
                paradas = fila.listar_aguardando_retorno(conn, status, tabela, self._settings.PCP_RECONCILIACAO_MINUTOS)
            guardadas = self._repassador.pendentes(tipo)
            for linha in paradas:
                if linha["id"] in guardadas:
                    continue
                if self._reconciliar(tipo, linha):
                    resolvidas += 1
        return resolvidas

    def _reconciliar(self, tipo: str, linha: dict) -> bool:
        registro_id = linha["id"]
        horas = float(linha["horas_esperando"] or 0)
        rotulo = f"{tipo} #{registro_id}"

        if linha["id_requisicao"]:
            try:
                resposta = self._erp.get(CAMINHO_REQUISICAO, {"id": linha["id_requisicao"]})
            except ErroErp as exc:
                logger.warning("Reconciliação %s: consulta da requisição %s falhou: %s", rotulo, linha["id_requisicao"], exc)
                return False
            consulta = ler_json(resposta)
            payload = extrair_payload_requisicao(consulta)
            if payload is not None:
                logger.info("Reconciliação %s: requisição %s já processada no ERP; repassando", rotulo, linha["id_requisicao"])
                self._repassar(tipo, registro_id, payload)
                return True
            logger.info(
                "Reconciliação %s: requisição %s ainda sem resultado no ERP (%s)",
                rotulo, linha["id_requisicao"], _situacao(consulta),
            )

        if horas < self._settings.PCP_RECONCILIACAO_LIMITE_HORAS:
            return False

        if linha["id_requisicao"]:
            mensagem = (
                f"O ERP não concluiu a requisição {linha['id_requisicao']} em "
                f"{self._settings.PCP_RECONCILIACAO_LIMITE_HORAS}h. Confira no ERP antes de reenviar."
            )
        else:
            mensagem = (
                "Sem confirmação do ERP (o despacho foi interrompido antes da resposta). "
                "Confira no ERP antes de reenviar: a chamada pode ter sido processada."
            )
        logger.warning("Reconciliação %s: parado há %.1fh, liberando com erro", rotulo, horas)
        self._repassar(tipo, registro_id, corpo_erro(mensagem))
        return True

    def _repassar(self, tipo: str, registro_id: int, corpo: dict) -> None:
        if tipo == "orcamentos":
            self._repassador.orcamento(registro_id, corpo)
        else:
            self._repassador.aprovacao(registro_id, corpo)
