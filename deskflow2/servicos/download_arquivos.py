"""Download dos arquivos da OP para a pasta de produção.

Só para aprovações concluídas de lotes que pediram "baixar arquivos na pasta
da OP" (decisão do clique "Enviar"). Para cada OP da aprovação:
  - os arquivos vêm de orcamento_api_itens_retorno (id_op + origem do item):
      * lote de ESCOLA    -> pedido_distribuicao_arquivos -> pedido_arquivos_pdf
        (Vercel Blob), e a pasta raiz é o nome da escola;
      * lote de INTEGRAÇÃO -> URLs no próprio integra_pedido_produtos
        (`arquivo_pdf` e, quando existirem, `design_capa_frente`/`design_capa_verso`;
        mockups e etiqueta ficam de fora), e a pasta raiz é
        "<integração> - <numero_pedido>";
  - são baixados numa pasta temporária dentro de DOWNLOAD_BASE_PATH e só
    depois publicados em DOWNLOAD_BASE_PATH/<pasta raiz>/<op>/, para a
    produção nunca ver uma pasta pela metade;
  - arquivo que já está na pasta final (tamanho > 0) não é baixado de novo, o
    que torna seguro repetir depois de uma falha;
  - cada origem x arquivo ganha uma linha em downloads_bremen.
No fim, o desfecho vai para o PageFlow (/retorno/aprovacoes/:id/downloads).
"""

import logging
import os
import re
import shutil
import tempfile
import time
from collections import OrderedDict
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from ..repositorios import fila
from ..repositorios.status import carregar_catalogo
from .repasse import Repassador

logger = logging.getLogger(__name__)

HOST_VERCEL_BLOB = "public.blob.vercel-storage.com"


def sanitizar_nome(nome: str, padrao: str) -> str:
    """Nome seguro para UM nível de pasta/arquivo no Windows: separadores e
    caracteres reservados viram "_" (nunca sobe de pasta), sem ponto final."""
    valor = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(nome or ""))
    valor = re.sub(r"\s+", " ", valor).strip().rstrip(". ")
    return valor or padrao


def chave_arquivo(linha: dict) -> str:
    """Identidade de um arquivo dentro da OP, nas duas origens de lote.

    Num lote de escola é o `arquivo_pdf_id` (o mesmo PDF pode servir a vários
    pedidos, e só se baixa uma vez). Num lote de integração não existe
    `pedido_arquivos_pdf`: a identidade é (produto, tipo do arquivo), que o
    SQL já devolve pronta em `chave_arquivo`.
    """
    chave = linha.get("chave_arquivo")
    if chave is not None:
        return str(chave)
    return str(linha.get("arquivo_pdf_id"))


def nomes_unicos(arquivos: list) -> list:
    """Nome final de cada arquivo da OP; dois arquivos diferentes com o mesmo
    nome ganham a chave do arquivo como sufixo, sem sobrescrever um ao outro."""
    usados = set()
    resultado = []
    for arquivo in arquivos:
        sufixo = sanitizar_nome(chave_arquivo(arquivo), "arquivo")
        nome = sanitizar_nome(arquivo["arquivo_nome"], f"arquivo_{sufixo}.pdf")
        if nome.lower() in usados:
            base, extensao = os.path.splitext(nome)
            nome = f"{base}_{sufixo}{extensao}"
        usados.add(nome.lower())
        resultado.append(nome)
    return resultado


def dentro_da_base(base: str, caminho: str) -> bool:
    base = os.path.abspath(base)
    return os.path.commonpath([base, os.path.abspath(caminho)]) == base


class BaixadorArquivos:
    def __init__(self, settings, http: Optional[httpx.Client] = None, dormir: Callable[[float], None] = time.sleep):
        self._token_blob = settings.BLOB_READ_WRITE_TOKEN
        self._tentativas = max(1, settings.DOWNLOAD_TENTATIVAS)
        self._http = http or httpx.Client(timeout=settings.DOWNLOAD_TIMEOUT, follow_redirects=True)
        self._dormir = dormir

    def _headers(self, url: str) -> dict:
        partes = urlparse(url)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            raise ValueError(f"URL de download inválida: {url!r}")
        host = partes.hostname.lower()
        # O token do Blob só vai para o próprio Blob, nunca para outro host.
        if self._token_blob and (host == HOST_VERCEL_BLOB or host.endswith("." + HOST_VERCEL_BLOB)):
            return {"Authorization": f"Bearer {self._token_blob}"}
        return {}

    def baixar(self, url: str, destino: str) -> int:
        headers = self._headers(url)
        ultimo_erro = None
        for tentativa in range(1, self._tentativas + 1):
            try:
                with self._http.stream("GET", url, headers=headers) as resposta:
                    resposta.raise_for_status()
                    with open(destino, "wb") as arquivo:
                        for pedaco in resposta.iter_bytes():
                            arquivo.write(pedaco)
                tamanho = os.path.getsize(destino)
                if tamanho <= 0:
                    raise ValueError("arquivo vazio")
                return tamanho
            except (httpx.HTTPError, OSError, ValueError) as exc:
                ultimo_erro = exc
                if os.path.exists(destino):
                    os.remove(destino)
                if tentativa < self._tentativas:
                    self._dormir(3 * tentativa)
        raise RuntimeError(f"falha ao baixar ({ultimo_erro})")


def publicar_arquivos(pasta_stage: str, pasta_final: str, dormir: Callable[[float], None] = time.sleep) -> None:
    """Pasta nova: rename atômico (repetido, porque compartilhamento de rede às
    vezes nega na primeira). Pasta existente, ou rename recusado: move arquivo
    a arquivo sem apagar o que já estava lá."""
    if not os.path.exists(pasta_final):
        for tentativa in range(1, 4):
            try:
                os.rename(pasta_stage, pasta_final)
                return
            except FileExistsError:
                break
            except OSError as exc:
                logger.warning("Rename de %s recusado (%s), tentativa %s/3", pasta_final, exc, tentativa)
                dormir(0.5 * tentativa)
    os.makedirs(pasta_final, exist_ok=True)
    for nome in sorted(os.listdir(pasta_stage)):
        shutil.move(os.path.join(pasta_stage, nome), os.path.join(pasta_final, nome))


class DownloadArquivos:
    def __init__(self, engine, baixador: BaixadorArquivos, repassador: Repassador, settings, dormir: Callable[[float], None] = time.sleep):
        self._engine = engine
        self._baixador = baixador
        self._repassador = repassador
        self._settings = settings
        self._dormir = dormir

    def executar_ciclo(self) -> int:
        if not self._settings.DOWNLOAD_BASE_PATH:
            return 0
        reinicio = self._settings.PCP_DOWNLOAD_REINICIO_MINUTOS
        with self._engine.begin() as conn:
            status = carregar_catalogo(conn)
            pendentes = fila.listar_downloads_pendentes(conn, status, reinicio, self._settings.PCP_ENVIO_LOTE_MAXIMO)
        processadas = 0
        for aprovacao_id in pendentes:
            with self._engine.begin() as conn:
                if not fila.reivindicar_download(conn, aprovacao_id, reinicio):
                    continue
            self.baixar_aprovacao(aprovacao_id)
            processadas += 1
        return processadas

    def baixar_aprovacao(self, aprovacao_id: int) -> dict:
        with self._engine.begin() as conn:
            arquivos = fila.arquivos_da_aprovacao(conn, aprovacao_id)

        if not arquivos:
            resultado = {
                "sucesso": False,
                "total_arquivos": 0,
                "erros": ["Nenhuma OP com arquivos vinculada a esta aprovação (confira ops[].codigo_externo no retorno do ERP)"],
            }
            self._repassador.downloads(aprovacao_id, resultado)
            return resultado

        base = os.path.abspath(self._settings.DOWNLOAD_BASE_PATH)
        os.makedirs(base, exist_ok=True)
        erros: list = []
        linhas_bremen: list = []
        total = 0

        por_op: "OrderedDict[int, list]" = OrderedDict()
        for linha in arquivos:
            por_op.setdefault(linha["id_op"], []).append(linha)

        temporaria = tempfile.mkdtemp(prefix=f".deskflow2-{aprovacao_id}-", dir=base)
        try:
            for id_op, linhas in por_op.items():
                total_op, erros_op, linhas_op = self._baixar_op(base, temporaria, id_op, linhas)
                total += total_op
                erros.extend(erros_op)
                linhas_bremen.extend(linhas_op)
        finally:
            shutil.rmtree(temporaria, ignore_errors=True)

        with self._engine.begin() as conn:
            fila.registrar_downloads_bremen(conn, linhas_bremen)

        resultado = {"sucesso": not erros, "total_arquivos": total, "erros": erros}
        logger.info("Aprovação #%s: %s arquivo(s) na pasta da OP, %s erro(s)", aprovacao_id, total, len(erros))
        self._repassador.downloads(aprovacao_id, resultado)
        return resultado

    def _baixar_op(self, base: str, temporaria: str, id_op: int, linhas: list):
        # Primeiro nível da pasta: a escola (lote de escola) ou
        # "<integração> - <numero_pedido>" (lote de integração). O SQL já
        # resolve qual dos dois e devolve em `pasta`.
        pasta_raiz = sanitizar_nome(linhas[0].get("pasta") or linhas[0].get("escola_nome"), "Sem nome")
        pasta_final = os.path.join(base, pasta_raiz, str(id_op))
        if not dentro_da_base(base, pasta_final):
            return 0, [f"OP {id_op}: caminho fora da pasta base"], []

        # Um arquivo pode servir a várias origens da mesma OP (modo por escola):
        # baixa uma vez só, e depois grava uma linha de downloads_bremen por origem.
        unicos: "OrderedDict[str, dict]" = OrderedDict()
        for linha in linhas:
            unicos.setdefault(chave_arquivo(linha), linha)
        arquivos = list(unicos.values())
        nomes = dict(zip((chave_arquivo(a) for a in arquivos), nomes_unicos(arquivos)))

        pasta_stage = os.path.join(temporaria, str(id_op))
        os.makedirs(pasta_stage, exist_ok=True)
        erros, tamanhos = [], {}
        for arquivo in arquivos:
            chave = chave_arquivo(arquivo)
            nome = nomes[chave]
            final = os.path.join(pasta_final, nome)
            if os.path.isfile(final) and os.path.getsize(final) > 0:
                tamanhos[chave] = os.path.getsize(final)
                continue
            if not arquivo.get("url"):
                erros.append(f"OP {id_op}: {nome} sem URL do arquivo")
                continue
            try:
                tamanhos[chave] = self._baixador.baixar(arquivo["url"], os.path.join(pasta_stage, nome))
            except (RuntimeError, ValueError) as exc:
                erros.append(f"OP {id_op}: {nome} — {exc}")

        if os.listdir(pasta_stage):
            os.makedirs(os.path.dirname(pasta_final), exist_ok=True)
            try:
                publicar_arquivos(pasta_stage, pasta_final, self._dormir)
            except OSError as exc:
                return 0, erros + [f"OP {id_op}: falha ao publicar a pasta — {exc}"], []

        # Arco exclusivo em downloads_bremen: um lado por linha, nunca os dois
        # (CHECK ck_downloads_bremen_origem).
        linhas_bremen = [
            {
                "distribuicao_material_id": linha.get("pedido_distribuicao_id"),
                "integra_pedido_produto_id": linha.get("integra_pedido_produto_id"),
                "id_ops": id_op,
                "arquivo_pdf_id": linha.get("arquivo_pdf_id"),
                "tipo_arquivo": linha.get("tipo_arquivo"),
                "caminho_local": os.path.join(pasta_final, nomes[chave_arquivo(linha)]),
                "tamanho": tamanhos[chave_arquivo(linha)],
            }
            for linha in linhas
            if chave_arquivo(linha) in tamanhos
        ]
        return len(tamanhos), erros, linhas_bremen
