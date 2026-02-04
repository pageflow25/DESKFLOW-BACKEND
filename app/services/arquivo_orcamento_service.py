import json
from pathlib import Path
from typing import Dict, Any, List
from ..config.logging_config import get_logger

logger = get_logger(__name__)


class ArquivoOrcamentoService:
    """Service para gerenciar arquivos de orçamento temporários"""
    
    # Diretório para armazenar arquivos temporários
    TEMP_DIR = Path(__file__).parent.parent.parent / "temp_orcamentos"
    
    @classmethod
    def criar_diretorio(cls):
        """Cria o diretório de arquivos temporários se não existir"""
        cls.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Diretório de orçamentos verificado: {cls.TEMP_DIR}")
    
    @classmethod
    def obter_caminho_arquivo(cls, nome_arquivo: str) -> Path:
        """
        Obtém o caminho completo de um arquivo de orçamento
        
        Args:
            nome_arquivo: Nome do arquivo
            
        Returns:
            Caminho completo do arquivo
        """
        return cls.TEMP_DIR / nome_arquivo
    
    @classmethod
    def listar_arquivos(cls) -> List[Dict[str, Any]]:
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
            orcamentos.sort(key=lambda x: x.get("gerado_em", ""), reverse=True)
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
        caminho = cls.obter_caminho_arquivo(nome_arquivo)
        
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
    def limpar_arquivos_antigos(cls, dias: int = 1) -> List[str]:
        """
        Deleta arquivos de orçamento mais antigos que X dias
        
        Args:
            dias: Número de dias (default: 1)
            
        Returns:
            Lista de nomes de arquivos deletados
        """
        import time
        
        cls.criar_diretorio()
        arquivos_removidos = []
        limite_tempo = time.time() - (dias * 24 * 60 * 60)
        
        try:
            for arquivo in cls.TEMP_DIR.glob("orcamento_*.json"):
                if arquivo.stat().st_mtime < limite_tempo:
                    try:
                        nome = arquivo.name
                        arquivo.unlink()
                        arquivos_removidos.append(nome)
                        logger.info(f"Arquivo antigo deletado: {nome}")
                    except Exception as e:
                        logger.warning(f"Erro ao deletar arquivo antigo {arquivo.name}: {str(e)}")
            
            logger.info(f"Total de arquivos antigos deletados: {len(arquivos_removidos)}")
            return arquivos_removidos
            
        except Exception as e:
            logger.error(f"Erro ao limpar arquivos antigos: {str(e)}", exc_info=True)
            return []
