"""Download dos arquivos da OP para a pasta de produção.

Só para aprovações concluídas de lotes que pediram "baixar arquivos na pasta
da OP" (decisão do clique "Enviar"). Para cada OP da aprovação:
  - os arquivos vêm de orcamento_api_itens_retorno (id_op + origem do item):
      * lote de ESCOLA    -> pedido_distribuicao_arquivos -> pedido_arquivos_pdf
        (Vercel Blob);
      * lote de INTEGRAÇÃO -> URLs no próprio integra_pedido_produtos
        (`arquivo_pdf` e, quando existirem, `design_capa_frente`/`design_capa_verso`;
        mockups e etiqueta ficam de fora);
  - cada OP vai direto para DOWNLOAD_BASE_PATH/<id_op>, sem pasta de
    escola/integração antes;
  - são baixados DIRETO na pasta da OP, como no DeskFlow antigo, para a pasta
    e os arquivos herdarem as permissões do compartilhamento da produção. Não
    há pasta temporária: a que existia (tempfile.mkdtemp) nascia com ACL só do
    usuário do serviço no Windows, e o rename levava essa ACL para a pasta da
    OP. Cada arquivo é escrito como "<nome>.part" na própria pasta da OP e
    renomeado ao terminar, então um arquivo pela metade nunca conta como
    baixado;
  - a extensão final segue o CONTEÚDO baixado (PDF/PNG/JPG/TIFF), não o nome
    que o SQL monta: design de capa em PNG não vira ".pdf" corrompido;
  - arquivo que já está na pasta final (tamanho > 0, com o nome original ou
    com a extensão corrigida) não é baixado de novo, o que torna seguro
    repetir depois de uma falha;
  - cada origem x arquivo ganha uma linha de `downloads_bremen` PRONTA, que
    sai no resultado do item da fila — quem grava é o PageFlow.

Quem orquestra é o handler `pcp.download_arquivos`
(`controllers/pcp/download_arquivos.py`); aqui ficam só as peças que levam o
arquivo até a pasta da OP.
"""

import logging
import os
import re
import time
from collections import OrderedDict
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

HOST_VERCEL_BLOB = "public.blob.vercel-storage.com"

# O nome do arquivo vem do SQL com extensão fixa (".pdf"), mas o conteúdo é o
# que a URL devolver: na integração o design da capa costuma ser PNG/JPG (a
# própria doc da API usa .png de exemplo). Salvar PNG como ".pdf" faz o arquivo
# parecer corrompido na produção. A extensão final segue o conteúdo real.
ASSINATURAS = (
    (b"%PDF-", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
)
EXTENSOES_CONHECIDAS = sorted({extensao for _, extensao in ASSINATURAS})
SINONIMOS_EXTENSAO = {".jpeg": ".jpg", ".tiff": ".tif"}


def extensao_pelo_conteudo(caminho: str) -> Optional[str]:
    """Extensão pelos primeiros bytes do arquivo; None se o tipo não é conhecido."""
    with open(caminho, "rb") as arquivo:
        inicio = arquivo.read(16)
    for assinatura, extensao in ASSINATURAS:
        if inicio.startswith(assinatura):
            return extensao
    return None


def corrigir_extensao(caminho: str) -> str:
    """Renomeia o arquivo para a extensão do conteúdo real e devolve o caminho
    final. Conteúdo desconhecido mantém o nome (com aviso no log); nunca
    sobrescreve outro arquivo da pasta."""
    real = extensao_pelo_conteudo(caminho)
    base, atual = os.path.splitext(caminho)
    if real is None:
        logger.warning("Arquivo %s com conteúdo de tipo desconhecido; nome mantido", caminho)
        return caminho
    if SINONIMOS_EXTENSAO.get(atual.lower(), atual.lower()) == real:
        return caminho
    novo = base + real
    if os.path.exists(novo):
        logger.warning("Arquivo %s é %s, mas %s já existe; nome mantido", caminho, real, novo)
        return caminho
    os.replace(caminho, novo)
    logger.info("Arquivo %s é %s pelo conteúdo; salvo como %s", caminho, real, novo)
    return novo


def arquivo_ja_baixado(final: str) -> Optional[str]:
    """Caminho do arquivo já presente na pasta da OP (tamanho > 0), com o nome
    original ou com a extensão corrigida em um download anterior."""
    base, _ = os.path.splitext(final)
    for caminho in [final] + [base + extensao for extensao in EXTENSOES_CONHECIDAS]:
        if os.path.isfile(caminho) and os.path.getsize(caminho) > 0:
            return caminho
    return None


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
        """Baixa em "<destino>.part", na mesma pasta do destino (herda as
        permissões dela), e só renomeia para o nome final quando completo."""
        headers = self._headers(url)
        parcial = destino + ".part"
        ultimo_erro = None
        for tentativa in range(1, self._tentativas + 1):
            try:
                with self._http.stream("GET", url, headers=headers) as resposta:
                    resposta.raise_for_status()
                    with open(parcial, "wb") as arquivo:
                        for pedaco in resposta.iter_bytes():
                            arquivo.write(pedaco)
                tamanho = os.path.getsize(parcial)
                if tamanho <= 0:
                    raise ValueError("arquivo vazio")
                os.replace(parcial, destino)
                return tamanho
            except (httpx.HTTPError, OSError, ValueError) as exc:
                ultimo_erro = exc
                if os.path.exists(parcial):
                    os.remove(parcial)
                if tentativa < self._tentativas:
                    self._dormir(3 * tentativa)
        raise RuntimeError(f"falha ao baixar ({ultimo_erro})")


MENSAGEM_SEM_ARQUIVOS = (
    "Nenhuma OP com arquivos vinculada a esta aprovação "
    "(confira ops[].codigo_externo no retorno do ERP)"
)


def baixar_arquivos_das_ops(baixador: BaixadorArquivos, pasta_base: str, arquivos: list):
    """Baixa os arquivos de TODAS as OPs de uma aprovação e devolve
    `(total, erros, linhas_bremen)`.

    `linhas_bremen` sai PRONTA mas NÃO é gravada aqui: quem grava
    `downloads_bremen` é o PageFlow, a partir do resultado do item da fila.
    """
    base = os.path.abspath(pasta_base)
    os.makedirs(base, exist_ok=True)
    erros: list = []
    linhas_bremen: list = []
    total = 0

    por_op: "OrderedDict[int, list]" = OrderedDict()
    for linha in arquivos:
        por_op.setdefault(linha["id_op"], []).append(linha)

    for id_op, linhas in por_op.items():
        total_op, erros_op, linhas_op = baixar_op(baixador, base, id_op, linhas)
        total += total_op
        erros.extend(erros_op)
        linhas_bremen.extend(linhas_op)

    return total, erros, linhas_bremen


def baixar_op(baixador: BaixadorArquivos, base: str, id_op: int, linhas: list):
    # A pasta da OP fica direto na base, sem pasta de escola/integração antes.
    pasta_final = os.path.join(base, str(id_op))
    if not dentro_da_base(base, pasta_final):
        return 0, [f"OP {id_op}: caminho fora da pasta base"], []

    # Um arquivo pode servir a várias origens da mesma OP (o SQL Agrupado soma
    # unidades num item só):
    # baixa uma vez só, e depois grava uma linha de downloads_bremen por origem.
    unicos: "OrderedDict[str, dict]" = OrderedDict()
    for linha in linhas:
        unicos.setdefault(chave_arquivo(linha), linha)
    arquivos = list(unicos.values())
    nomes = dict(zip((chave_arquivo(a) for a in arquivos), nomes_unicos(arquivos)))

    erros, tamanhos, caminhos, pendentes = [], {}, {}, []
    for arquivo in arquivos:
        chave = chave_arquivo(arquivo)
        nome = nomes[chave]
        final = os.path.join(pasta_final, nome)
        existente = arquivo_ja_baixado(final)
        if existente:
            tamanhos[chave] = os.path.getsize(existente)
            caminhos[chave] = existente
            continue
        if not arquivo.get("url"):
            erros.append(f"OP {id_op}: {nome} sem URL do arquivo")
            continue
        pendentes.append((chave, nome, arquivo["url"], final))

    if pendentes:
        try:
            os.makedirs(pasta_final, exist_ok=True)
        except OSError as exc:
            return 0, erros + [f"OP {id_op}: falha ao criar a pasta — {exc}"], []
    for chave, nome, url, final in pendentes:
        try:
            tamanhos[chave] = baixador.baixar(url, final)
            caminhos[chave] = corrigir_extensao(final)
        except (RuntimeError, ValueError, OSError) as exc:
            tamanhos.pop(chave, None)
            erros.append(f"OP {id_op}: {nome} — {exc}")

    # Arco exclusivo em downloads_bremen: um lado por linha, nunca os dois
    # (CHECK ck_downloads_bremen_origem).
    linhas_bremen = [
        {
            "distribuicao_material_id": linha.get("pedido_distribuicao_id"),
            "integra_pedido_produto_id": linha.get("integra_pedido_produto_id"),
            "id_ops": id_op,
            "arquivo_pdf_id": linha.get("arquivo_pdf_id"),
            "tipo_arquivo": linha.get("tipo_arquivo"),
            "caminho_local": caminhos[chave_arquivo(linha)],
            "tamanho": tamanhos[chave_arquivo(linha)],
        }
        for linha in linhas
        if chave_arquivo(linha) in tamanhos
    ]
    return len(tamanhos), erros, linhas_bremen
