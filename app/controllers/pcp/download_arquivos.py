"""`pcp.download_arquivos` — baixa os arquivos da OP para a pasta de produção.

O único dos três tipos de PCP que é IDEMPOTENTE, e não por otimismo: a pasta
final é montada numa temporária e só publicada no fim, e arquivo que já está lá
com tamanho > 0 não é baixado de novo. Repetir depois de uma falha parcial
completa o que faltou sem sobrescrever o que já estava certo — é o que permite
ao motor retentar sozinho.

Este handler não fala com o ERP: as OPs e seus arquivos vêm do banco
(`repositorios/pcp.py::arquivos_da_aprovacao`, o mesmo SQL de antes, SELECT) e
os bytes vêm do Vercel Blob ou das URLs do produto de integração.

O que muda em relação ao ciclo antigo (`servicos/download_arquivos.py`): o
worker NÃO grava mais `downloads_bremen`. As linhas continuam sendo montadas —
uma por origem x arquivo, com o arco exclusivo do CHECK — mas saem em
`resultado.arquivos`, para o projetor do PageFlow inseri-las. Nenhuma coluna de
domínio é tocada aqui.

Payload: `{aprovacao_id, orcamento_id, id_orcamento}`.
Resultado: `{sucesso, total_arquivos, erros: [], arquivos: [...]}`.
"""

import logging
import time
from typing import Any, Callable, Optional

from ...fila.modelos import Desfecho, ItemReivindicado, Preparo
from ...repositorios import pcp as repositorio_pcp
from ...servicos.pcp.download_arquivos import (
    MENSAGEM_SEM_ARQUIVOS,
    BaixadorArquivos,
    baixar_arquivos_das_ops,
)
from ..payload_invalido import PayloadInvalido
from .validators import pcp_validator

logger = logging.getLogger(__name__)

TIPO = "pcp.download_arquivos"


class SemArquivos:
    """Nenhuma OP com arquivo vinculada à aprovação: nada a baixar."""


class Baixados:
    def __init__(self, total: int, erros: list, arquivos: list):
        self.total = total
        self.erros = erros
        self.arquivos = arquivos

    def resultado(self, sucesso: bool) -> dict:
        return {
            "sucesso": sucesso,
            "total_arquivos": self.total,
            "erros": self.erros,
            # As linhas de `downloads_bremen` prontas para o PageFlow gravar: o
            # worker não escreve em tabela de domínio.
            "arquivos": self.arquivos,
        }


class HandlerPcpDownloadArquivos:
    tipo = TIPO

    def __init__(self, baixador: BaixadorArquivos, pasta_base: str,
                 dormir: Callable[[float], None] = time.sleep):
        self._baixador = baixador
        self._pasta_base = pasta_base
        self._dormir = dormir

    def preparar(self, conn, item: ItemReivindicado) -> Preparo:
        aprovacao_id, invalido = pcp_validator.download_do_payload(item.payload)
        if invalido is not None:
            return invalido.preparo()

        # Leitura na transação curta do preparo; os bytes só começam a andar
        # dentro do `chamar`, fora de qualquer transação.
        arquivos = repositorio_pcp.arquivos_da_aprovacao(conn, aprovacao_id)
        ops = sorted({linha["id_op"] for linha in arquivos})
        return Preparo(
            payload_enviado={
                "aprovacao_id": aprovacao_id,
                "ops": ops,
                "arquivos_previstos": len(arquivos),
                "pasta_base": self._pasta_base,
            },
            chamar=lambda: self._baixar(aprovacao_id, arquivos),
        )

    def _baixar(self, aprovacao_id: int, arquivos: list):
        if not arquivos:
            return SemArquivos()
        total, erros, linhas = baixar_arquivos_das_ops(
            self._baixador, self._pasta_base, arquivos, str(aprovacao_id), self._dormir)
        logger.info("Aprovação #%s: %s arquivo(s) na pasta da OP, %s erro(s)",
                    aprovacao_id, total, len(erros))
        return Baixados(total, erros, linhas)

    def interpretar(self, item: ItemReivindicado, bruto: Any) -> Desfecho:
        if isinstance(bruto, PayloadInvalido):
            return bruto.desfecho()

        if isinstance(bruto, SemArquivos):
            # Definitivo: o vínculo OP -> origem vem do retorno do ERP, já
            # gravado quando este item foi enfileirado. Se não há arquivo agora,
            # não haverá na quinta tentativa — o que falta é conferir
            # `ops[].codigo_externo`.
            return Desfecho.falhou(MENSAGEM_SEM_ARQUIVOS, "SEM_ARQUIVOS")

        if not bruto.erros:
            return Desfecho.concluido(bruto.resultado(sucesso=True))

        erros = "; ".join(bruto.erros)
        if item.ultima_tentativa:
            # Última tentativa: fecha com `sucesso: false` em vez de ir para a
            # DLQ. É de propósito — `falhar`/`dlq` não guardam `resultado`, e
            # aí o PageFlow perderia a lista de quais arquivos faltaram, que é
            # exatamente o que a tela precisa mostrar. Os arquivos que desceram
            # estão publicados e contam.
            logger.warning("Aprovação do item %s: %s erro(s) de download na última tentativa",
                           item.id, len(bruto.erros))
            return Desfecho.concluido(bruto.resultado(sucesso=False))

        # Falha parcial com tentativa sobrando: o download é idempotente, então
        # retentar completa o que faltou sem rebaixar o que já está na pasta.
        return Desfecho.retentar(f"{len(bruto.erros)} arquivo(s) não baixado(s): {erros}",
                                 "DOWNLOAD_PARCIAL")

    def verificar(self, conn, item: ItemReivindicado) -> Optional[Desfecho]:
        """Tipo idempotente: o motor já retenta sozinho um resultado incerto,
        não há o que verificar."""
        return None
