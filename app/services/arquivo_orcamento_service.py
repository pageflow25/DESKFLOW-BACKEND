import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from ..config.logging_config import get_logger

logger = get_logger(__name__)


class ArquivoOrcamentoService:
    """Service para gerenciar arquivos de orçamento"""
    
    # Diretório para armazenar arquivos temporários
    TEMP_DIR = Path(__file__).parent.parent.parent / "temp_orcamentos"
    
    @classmethod
    def criar_diretorio(cls):
        """Cria o diretório de arquivos temporários se não existir"""
        cls.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Diretório de orçamentos verificado: {cls.TEMP_DIR}")
    
    @classmethod
    def salvar_orcamento(
        cls, 
        orcamentos: List[Dict[str, Any]], 
        escola_id: int,
        ids_produtos: List[int]
    ) -> str:
        """
        Salva os orçamentos em um arquivo JSON temporário
        
        Args:
            orcamentos: Lista de orçamentos gerados
            escola_id: ID da escola
            ids_produtos: Lista de IDs de produtos
            
        Returns:
            Caminho relativo do arquivo salvo
        """
        cls.criar_diretorio()
        
        # Gerar nome único do arquivo
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        produtos_str = "_".join(map(str, ids_produtos[:3]))  # Primeiros 3 produtos
        nome_arquivo = f"orcamento_escola_{escola_id}_prods_{produtos_str}_{timestamp}.json"
        caminho_completo = cls.TEMP_DIR / nome_arquivo
        
        # Preparar dados para salvar
        dados = {
            "gerado_em": datetime.now().isoformat(),
            "escola_id": escola_id,
            "ids_produtos": ids_produtos,
            "total_unidades": len(orcamentos),
            "orcamentos": orcamentos
        }
        
        # Salvar arquivo
        try:
            with open(caminho_completo, 'w', encoding='utf-8') as f:
                json.dump(dados, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Orçamento salvo em: {caminho_completo}")
            return nome_arquivo
            
        except Exception as e:
            logger.error(f"Erro ao salvar orçamento em arquivo: {str(e)}", exc_info=True)
            raise
    
    @classmethod
    def obter_caminho_completo(cls, nome_arquivo: str) -> Path:
        """
        Obtém o caminho completo de um arquivo de orçamento
        
        Args:
            nome_arquivo: Nome do arquivo
            
        Returns:
            Caminho completo do arquivo
        """
        return cls.TEMP_DIR / nome_arquivo
    
    @classmethod
    def arquivo_existe(cls, nome_arquivo: str) -> bool:
        """
        Verifica se um arquivo de orçamento existe
        
        Args:
            nome_arquivo: Nome do arquivo
            
        Returns:
            True se existe, False caso contrário
        """
        caminho = cls.obter_caminho_completo(nome_arquivo)
        return caminho.exists()
    
    @classmethod
    def obter_conteudo(cls, nome_arquivo: str) -> Dict[str, Any]:
        """
        Obtém o conteúdo de um arquivo de orçamento
        
        Args:
            nome_arquivo: Nome do arquivo
            
        Returns:
            Dicionário com dados do orçamento
        """
        caminho = cls.obter_caminho_completo(nome_arquivo)
        
        if not caminho.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {nome_arquivo}")
        
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao ler orçamento: {str(e)}", exc_info=True)
            raise
    
    @classmethod
    def listar_orcamentos(cls) -> List[Dict[str, Any]]:
        """
        Lista todos os arquivos de orçamento disponíveis
        
        Returns:
            Lista com informações dos arquivos
        """
        cls.criar_diretorio()
        orcamentos = []
        
        try:
            for arquivo in cls.TEMP_DIR.glob("orcamento_*.json"):
                try:
                    with open(arquivo, 'r', encoding='utf-8') as f:
                        dados = json.load(f)
                    
                    orcamentos.append({
                        "nome_arquivo": arquivo.name,
                        "gerado_em": dados.get("gerado_em"),
                        "escola_id": dados.get("escola_id"),
                        "total_unidades": dados.get("total_unidades"),
                        "tamanho_kb": arquivo.stat().st_size / 1024
                    })
                except Exception as e:
                    logger.warning(f"Erro ao ler arquivo {arquivo.name}: {str(e)}")
            
            # Ordenar por data decrescente
            orcamentos.sort(key=lambda x: x["gerado_em"], reverse=True)
            return orcamentos
            
        except Exception as e:
            logger.error(f"Erro ao listar orçamentos: {str(e)}", exc_info=True)
            return []
    
    @classmethod
    def deletar_arquivo(cls, nome_arquivo: str) -> bool:
        """
        Deleta um arquivo de orçamento
        
        Args:
            nome_arquivo: Nome do arquivo
            
        Returns:
            True se deletado, False caso contrário
        """
        caminho = cls.obter_caminho_completo(nome_arquivo)
        
        if not caminho.exists():
            return False
        
        try:
            caminho.unlink()
            logger.info(f"Arquivo deletado: {nome_arquivo}")
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar arquivo {nome_arquivo}: {str(e)}", exc_info=True)
            return False
    
    @classmethod
    def limpar_arquivos_antigos(cls, dias: int = 1) -> int:
        """
        Deleta arquivos de orçamento mais antigos que X dias
        
        Args:
            dias: Número de dias (default: 1)
            
        Returns:
            Número de arquivos deletados
        """
        from datetime import timedelta
        import time
        
        cls.criar_diretorio()
        deletados = 0
        limite_tempo = time.time() - (dias * 24 * 60 * 60)
        
        try:
            for arquivo in cls.TEMP_DIR.glob("orcamento_*.json"):
                if arquivo.stat().st_mtime < limite_tempo:
                    try:
                        arquivo.unlink()
                        deletados += 1
                        logger.info(f"Arquivo antigo deletado: {arquivo.name}")
                    except Exception as e:
                        logger.warning(f"Erro ao deletar arquivo antigo {arquivo.name}: {str(e)}")
            
            logger.info(f"Total de arquivos antigos deletados: {deletados}")
            return deletados
            
        except Exception as e:
            logger.error(f"Erro ao limpar arquivos antigos: {str(e)}", exc_info=True)
            return 0
