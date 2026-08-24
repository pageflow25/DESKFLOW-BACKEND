import os
import re
import httpx
import asyncio
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..models.download_bremen import DownloadBremen
from ..models.aprovacao_api import AprovacaoAPI
from ..models.distribuicao_material import DistribuicaoMaterial
from ..models.arquivo_pdf import ArquivoPdf

logger = get_logger(__name__)
settings = get_settings()

# Configurações de download
DOWNLOAD_TIMEOUT = 120  # Timeout para download de arquivos (segundos)
MAX_DOWNLOAD_RETRIES = 3
RETRY_DELAY_SECONDS = 3


class DownloadBremenService:
    """
    Service para FASE 03 — Baixar e Organizar Arquivos por OP.
    
    Após a aprovação (Fase 2), cada item recebe um número de OP.
    Esta fase baixa os PDFs do Vercel Blob e organiza em pastas nomeadas pelo número da OP.
    
    Estrutura gerada:
        {DOWNLOAD_BASE_PATH}/
        ├── 4777/
        │   └── ATIV_23_02.PDF                    ← somente miolo
        ├── 4800/
        │   ├── BLOCO_ATIVIDADES_CAPA.PDF         ← capa
        │   └── BLOCO_ATIVIDADES_MIOLO.PDF        ← miolo
    """
    
    def __init__(self):
        self.download_base_path = getattr(settings, 'DOWNLOAD_BASE_PATH', 'C:/Bremen/OPs')
    
    async def processar_downloads_por_orcamento(
        self,
        db: Session,
        id_orcamento: int,
        organizar_por_escola: bool = False,
    ) -> Dict[str, Any]:
        """
        Processa downloads de todos os OPs de um orçamento aprovado.
        
        Args:
            db: Sessão do banco de dados
            id_orcamento: ID do orçamento aprovado
            
        Returns:
            Dict com resumo do processamento
        """
        logger.info(f"FASE 03 — Iniciando downloads para orçamento {id_orcamento}")
        
        # Buscar todas as aprovações deste orçamento
        aprovacoes = db.query(AprovacaoAPI).filter(
            AprovacaoAPI.id_orcamento == id_orcamento
        ).order_by(AprovacaoAPI.id).all()
        
        if not aprovacoes:
            logger.warning(f"Nenhuma aprovação encontrada para orçamento {id_orcamento}")
            return {
                "id_orcamento": id_orcamento,
                "total_ops": 0,
                "downloads": 0,
                "erros": ["Nenhuma aprovação encontrada"]
            }
        
        resultado = {
            "id_orcamento": id_orcamento,
            "total_ops": len(aprovacoes),
            "downloads": 0,
            "erros": [],
            "detalhes": []
        }
        
        for aprovacao in aprovacoes:
            try:
                detalhes_op = await self._processar_op(db, aprovacao, organizar_por_escola)
                resultado["downloads"] += detalhes_op.get("arquivos_baixados", 0)
                resultado["detalhes"].append(detalhes_op)
            except Exception as e:
                error_msg = f"Erro ao processar OP {aprovacao.id_ops}: {str(e)}"
                logger.error(error_msg)
                resultado["erros"].append(error_msg)
                resultado["detalhes"].append({
                    "id_ops": aprovacao.id_ops,
                    "distribuicao_material_id": aprovacao.distribuicao_material_id,
                    "status": "erro",
                    "erro": str(e)
                })
        
        logger.info(
            f"FASE 03 concluída para orçamento {id_orcamento}: "
            f"{resultado['downloads']} arquivos baixados, "
            f"{len(resultado['erros'])} erros"
        )
        
        return resultado
    
    async def _processar_op(
        self,
        db: Session,
        aprovacao: AprovacaoAPI,
        organizar_por_escola: bool = False,
    ) -> Dict[str, Any]:
        """
        Processa download de arquivos para uma OP específica.
        
        Fluxo:
        1. Busca a distribuição de material e seu arquivo_pdf
        2. Cria a pasta da OP
        3. Baixa o arquivo principal (miolo ou capa)
        4. Verifica se existe arquivo complementar (capa ↔ miolo)
        5. Registra no banco (downloads_bremen)
        
        Args:
            db: Sessão do banco de dados
            aprovacao: Registro de aprovação com id_ops e distribuicao_material_id
            
        Returns:
            Dict com detalhes do processamento da OP
        """
        op_id = aprovacao.id_ops
        dist_id = aprovacao.distribuicao_material_id
        
        logger.info(f"Processando OP {op_id} (distribuição {dist_id})")
        
        # 1. Buscar a distribuição e seu arquivo_pdf
        distribuicao = db.query(DistribuicaoMaterial).filter(
            DistribuicaoMaterial.id == dist_id
        ).first()
        
        if not distribuicao:
            raise ValueError(f"Distribuição {dist_id} não encontrada")
        
        if not distribuicao.arquivo_pdf_id:
            raise ValueError(f"Distribuição {dist_id} não tem arquivo_pdf_id vinculado")
        
        arquivo_principal = db.query(ArquivoPdf).filter(
            ArquivoPdf.id == distribuicao.arquivo_pdf_id
        ).first()
        
        if not arquivo_principal:
            raise ValueError(f"Arquivo PDF {distribuicao.arquivo_pdf_id} não encontrado")
        
        logger.info(
            f"OP {op_id}: arquivo principal id={arquivo_principal.id}, "
            f"tipo={arquivo_principal.tipo_arquivo}, nome={arquivo_principal.nome}"
        )
        
        # 2. Criar a pasta da OP
        pasta_base = self.download_base_path
        if organizar_por_escola:
            escola_nome = self._obter_nome_escola(db, distribuicao)
            pasta_base = os.path.join(pasta_base, self._sanitizar_nome_pasta(escola_nome or "Escola sem nome"))

        pasta_op = os.path.join(pasta_base, str(op_id))

        download_existente = db.query(DownloadBremen).filter(
            DownloadBremen.id_ops == op_id,
            DownloadBremen.distribuicao_material_id == dist_id
        ).order_by(DownloadBremen.id.desc()).first()

        if download_existente and download_existente.caminho_local:
            pasta_existente = os.path.dirname(download_existente.caminho_local)
            if os.path.normcase(os.path.normpath(pasta_existente)) == os.path.normcase(os.path.normpath(pasta_op)):
                logger.info(f"OP {op_id} já foi baixada anteriormente na organização atual, pulando")
                return {
                    "id_ops": op_id,
                    "distribuicao_material_id": dist_id,
                    "pasta_op": pasta_op,
                    "status": "ja_baixado",
                    "arquivos_baixados": 0
                }

        os.makedirs(pasta_op, exist_ok=True)
        logger.info(f"Pasta criada: {pasta_op}")
        
        arquivos_baixados = 0
        registros_salvos = []
        
        # 3. Baixar o arquivo principal
        url_principal = arquivo_principal.caminho_remoto or arquivo_principal.arquivo
        if url_principal:
            destino_principal = os.path.join(pasta_op, arquivo_principal.nome)
            tamanho = await self.baixar_arquivo(url_principal, destino_principal)
            
            # Registrar no banco
            registro = self._salvar_download(
                db=db,
                distribuicao_material_id=dist_id,
                id_ops=op_id,
                arquivo_pdf_id=arquivo_principal.id,
                tipo_arquivo=arquivo_principal.tipo_arquivo,
                caminho_local=destino_principal,
                tamanho=tamanho
            )
            registros_salvos.append(registro)
            arquivos_baixados += 1
            
            logger.info(f"✅ Baixado: {arquivo_principal.nome} ({tamanho} bytes)")
        else:
            logger.warning(f"Arquivo principal {arquivo_principal.id} sem URL de download")
        
        # 4. Verificar se existe arquivo complementar (capa ↔ miolo)
        #    CORREÇÃO: usa coluna `pares` para encontrar o par correto
        #    (antes filtrava apenas por formulario_id, misturando pares diferentes)
        if arquivo_principal.formulario_id:
            from .pareamento_pdf_service import buscar_par_complementar
            arquivo_complementar = buscar_par_complementar(db, arquivo_principal)
            
            if arquivo_complementar:
                url_complementar = arquivo_complementar.caminho_remoto or arquivo_complementar.arquivo
                if url_complementar:
                    destino_complementar = os.path.join(pasta_op, arquivo_complementar.nome)
                    tamanho_comp = await self.baixar_arquivo(url_complementar, destino_complementar)
                    
                    # Registrar no banco
                    registro_comp = self._salvar_download(
                        db=db,
                        distribuicao_material_id=dist_id,
                        id_ops=op_id,
                        arquivo_pdf_id=arquivo_complementar.id,
                        tipo_arquivo=arquivo_complementar.tipo_arquivo,
                        caminho_local=destino_complementar,
                        tamanho=tamanho_comp
                    )
                    registros_salvos.append(registro_comp)
                    arquivos_baixados += 1
                    
                    logger.info(
                        f"✅ Baixado complementar ({arquivo_complementar.tipo_arquivo}): "
                        f"{arquivo_complementar.nome} ({tamanho_comp} bytes)"
                    )
                else:
                    logger.warning(
                        f"Arquivo complementar {arquivo_complementar.id} sem URL de download"
                    )
            else:
                logger.debug(
                    f"OP {op_id}: sem arquivo complementar "
                    f"no formulário {arquivo_principal.formulario_id}"
                )
        
        db.commit()
        
        return {
            "id_ops": op_id,
            "distribuicao_material_id": dist_id,
            "pasta_op": pasta_op,
            "arquivos_baixados": arquivos_baixados,
            "registros_ids": [r.id for r in registros_salvos],
            "status": "sucesso"
        }

    @staticmethod
    def _sanitizar_nome_pasta(nome: str) -> str:
        valor = (nome or "").strip()
        valor = re.sub(r'[<>:"/\\|?*]+', '_', valor)
        valor = re.sub(r'\s+', ' ', valor).strip().rstrip('.')
        return valor or "Escola sem nome"

    @staticmethod
    def _obter_nome_escola(db: Session, distribuicao: DistribuicaoMaterial) -> Optional[str]:
        if not distribuicao.unidade_escolar_id:
            return None

        row = db.execute(
            text(
                """
                SELECT e.nome AS escola_nome
                FROM escola_unidades ue
                JOIN escola_escolas e ON e.id = ue.escola_id
                WHERE ue.id = :unidade_id
                """
            ),
            {"unidade_id": distribuicao.unidade_escolar_id},
        ).mappings().first()

        return row["escola_nome"] if row else None
    
    @staticmethod
    def _preparar_download(url: str) -> tuple[str, Dict[str, str]]:
        """Valida a URL e aplica autenticação somente ao Vercel Blob."""
        url_normalizada = str(url or "").strip()
        parsed = urlparse(url_normalizada)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL de download inválida")

        headers: Dict[str, str] = {}
        hostname = parsed.hostname.lower()
        blob_token = getattr(settings, "BLOB_READ_WRITE_TOKEN", "")
        if blob_token and (
            hostname == "public.blob.vercel-storage.com"
            or hostname.endswith(".public.blob.vercel-storage.com")
        ):
            headers["Authorization"] = f"Bearer {blob_token}"

        return url_normalizada, headers

    async def baixar_arquivo(self, url: str, destino: str) -> int:
        """
        Baixa arquivo do Vercel Blob Storage e salva localmente com retry.
        
        As URLs do Vercel Blob (*.public.blob.vercel-storage.com) são públicas,
        mas caso BLOB_READ_WRITE_TOKEN esteja configurado, será enviado como
        header de autorização para acesso a blobs privados.
        
        Args:
            url: URL pública do arquivo (Vercel Blob)
            destino: Caminho local de destino
            
        Returns:
            Tamanho do arquivo em bytes
        """
        url_normalizada, headers = self._preparar_download(url)
        last_error = None
        
        for tentativa in range(1, MAX_DOWNLOAD_RETRIES + 1):
            try:
                logger.debug(
                    "Download tentativa %s/%s: %s",
                    tentativa,
                    MAX_DOWNLOAD_RETRIES,
                    url_normalizada,
                )
                
                async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                    async with client.stream("GET", url_normalizada, headers=headers) as response:
                        response.raise_for_status()
                        with open(destino, "wb") as arquivo:
                            async for chunk in response.aiter_bytes():
                                arquivo.write(chunk)
                    
                    tamanho = os.path.getsize(destino)
                    return tamanho
                    
            except Exception as e:
                last_error = e
                if os.path.isfile(destino):
                    try:
                        os.remove(destino)
                    except OSError:
                        logger.warning("Não foi possível remover download parcial: %s", destino)
                logger.warning(
                    "Erro no download (tentativa %s/%s): %s",
                    tentativa,
                    MAX_DOWNLOAD_RETRIES,
                    str(e),
                )
                if tentativa < MAX_DOWNLOAD_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * tentativa)
        
        raise last_error if last_error else Exception(f"Falha ao baixar {url_normalizada}")

    async def _baixar_arquivo(self, url: str, destino: str) -> int:
        """Compatibilidade com chamadas antigas; prefira ``baixar_arquivo``."""
        return await self.baixar_arquivo(url, destino)
    
    def _salvar_download(
        self,
        db: Session,
        distribuicao_material_id: int,
        id_ops: int,
        arquivo_pdf_id: int,
        tipo_arquivo: str,
        caminho_local: str,
        tamanho: int
    ) -> DownloadBremen:
        """
        Salva registro de download na tabela downloads_bremen.
        
        Args:
            db: Sessão do banco de dados
            distribuicao_material_id: ID da distribuição
            id_ops: Número da OP
            arquivo_pdf_id: FK para arquivo_pdfs
            tipo_arquivo: 'capa' ou 'miolo'
            caminho_local: Caminho completo do arquivo salvo
            tamanho: Tamanho do arquivo em bytes
            
        Returns:
            DownloadBremen: Registro salvo
        """
        registro = DownloadBremen(
            distribuicao_material_id=distribuicao_material_id,
            id_ops=id_ops,
            arquivo_pdf_id=arquivo_pdf_id,
            tipo_arquivo=tipo_arquivo,
            caminho_local=caminho_local,
            tamanho=tamanho
        )
        db.add(registro)
        db.flush()  # Flush para obter o ID sem commit (commit é feito no chamador)
        
        logger.debug(
            f"Registro de download criado: id={registro.id}, op={id_ops}, "
            f"tipo={tipo_arquivo}, tamanho={tamanho}"
        )
        
        return registro
    
    @staticmethod
    def obter_downloads_por_orcamento(
        db: Session,
        id_orcamento: int
    ) -> List[Dict[str, Any]]:
        """
        Consulta downloads realizados para um orçamento.
        
        Args:
            db: Sessão do banco de dados
            id_orcamento: ID do orçamento
            
        Returns:
            Lista de downloads com detalhes
        """
        query = text("""
            SELECT 
                db.id,
                db.distribuicao_material_id,
                db.id_ops,
                db.arquivo_pdf_id,
                db.tipo_arquivo,
                db.caminho_local,
                db.tamanho,
                db.criado_em,
                ap.nome AS arquivo_nome,
                aa.id_orcamento
            FROM downloads_bremen db
            JOIN aprovacao_api aa ON aa.id_ops = db.id_ops 
                AND aa.distribuicao_material_id = db.distribuicao_material_id
            LEFT JOIN pedido_arquivos_pdf ap ON ap.id = db.arquivo_pdf_id
            WHERE aa.id_orcamento = :id_orcamento
            ORDER BY db.id_ops, db.tipo_arquivo
        """)
        
        result = db.execute(query, {"id_orcamento": id_orcamento})
        downloads = []
        
        for row in result:
            downloads.append({
                "id": row.id,
                "distribuicao_material_id": row.distribuicao_material_id,
                "id_ops": row.id_ops,
                "arquivo_pdf_id": row.arquivo_pdf_id,
                "tipo_arquivo": row.tipo_arquivo,
                "caminho_local": row.caminho_local,
                "tamanho": row.tamanho,
                "criado_em": str(row.criado_em) if row.criado_em else None,
                "arquivo_nome": row.arquivo_nome,
                "id_orcamento": row.id_orcamento
            })
        
        return downloads
