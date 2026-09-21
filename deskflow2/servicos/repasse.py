"""Repasse dos resultados ao PageFlow, sem perder nenhum.

Quando o PageFlow não recebe (fora do ar, chave recusada), o corpo é guardado
em `DADOS_DIR/repasses_pendentes/` e reenviado pela reconciliação. Enquanto
estiver guardado, a linha continua `aguardando_retorno` no banco e a
reconciliação não a marca como erro.
"""

import json
import logging
import os
import tempfile
from typing import Optional

from ..clientes.pageflow import Desfecho, PageflowClient, ResultadoRepasse

logger = logging.getLogger(__name__)


class Repassador:
    def __init__(self, pageflow: PageflowClient, dados_dir: str):
        self._pageflow = pageflow
        self._pasta = os.path.join(dados_dir, "repasses_pendentes")
        os.makedirs(self._pasta, exist_ok=True)

    # --- API usada pelos despachos --------------------------------------------

    def orcamento(self, orcamento_id: int, corpo: dict) -> ResultadoRepasse:
        return self._repassar("orcamentos", orcamento_id, corpo)

    def aprovacao(self, aprovacao_id: int, corpo: dict) -> ResultadoRepasse:
        return self._repassar("aprovacoes", aprovacao_id, corpo)

    def consulta_previa(self, aprovacao_id: int, corpo: dict) -> ResultadoRepasse:
        # Só auditoria: se não chegar, não vale guardar.
        return self._pageflow.consulta_previa(aprovacao_id, corpo)

    def downloads(self, aprovacao_id: int, corpo: dict) -> ResultadoRepasse:
        return self._repassar("downloads", aprovacao_id, corpo)

    # --- Pendentes ------------------------------------------------------------

    def pendentes(self, tipo: str) -> set:
        prefixo = f"{tipo}-"
        return {
            int(nome[len(prefixo):-len(".json")])
            for nome in os.listdir(self._pasta)
            if nome.startswith(prefixo) and nome.endswith(".json")
        }

    def reenviar_pendentes(self) -> int:
        entregues = 0
        for nome in sorted(os.listdir(self._pasta)):
            if not nome.endswith(".json"):
                continue
            caminho = os.path.join(self._pasta, nome)
            try:
                with open(caminho, encoding="utf-8") as arquivo:
                    registro = json.load(arquivo)
            except (OSError, ValueError) as exc:
                logger.error("Repasse pendente ilegível %s: %s", caminho, exc)
                continue
            resultado = self._enviar(registro["tipo"], registro["id"], registro["corpo"])
            if resultado.desfecho is not Desfecho.NAO_ENTREGUE:
                os.remove(caminho)
                entregues += 1
        return entregues

    # --- Interno ----------------------------------------------------------------

    def _enviar(self, tipo: str, registro_id: int, corpo: dict) -> ResultadoRepasse:
        if tipo == "orcamentos":
            return self._pageflow.retorno_orcamento(registro_id, corpo)
        if tipo == "aprovacoes":
            return self._pageflow.retorno_aprovacao(registro_id, corpo)
        if tipo == "downloads":
            return self._pageflow.downloads(registro_id, corpo)
        raise ValueError(f"Tipo de repasse desconhecido: {tipo}")

    def _repassar(self, tipo: str, registro_id: int, corpo: dict) -> ResultadoRepasse:
        resultado = self._enviar(tipo, registro_id, corpo)
        if resultado.desfecho is Desfecho.NAO_ENTREGUE:
            self._guardar(tipo, registro_id, corpo)
            logger.error("PageFlow não recebeu %s #%s; guardado para reenvio", tipo, registro_id)
        return resultado

    def _guardar(self, tipo: str, registro_id: int, corpo: dict) -> None:
        destino = os.path.join(self._pasta, f"{tipo}-{registro_id}.json")
        descritor, temporario = tempfile.mkstemp(dir=self._pasta, suffix=".tmp")
        with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
            json.dump({"tipo": tipo, "id": registro_id, "corpo": corpo}, arquivo, ensure_ascii=False)
        os.replace(temporario, destino)


def extrair_id_requisicao(corpo) -> Optional[int]:
    if not isinstance(corpo, dict):
        return None
    dados = corpo.get("data") if isinstance(corpo.get("data"), dict) else {}
    valor = dados.get("id_requisicao", corpo.get("id_requisicao"))
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None
