"""
Script para inicializar o banco de dados e criar todas as tabelas
"""
import sys
from pathlib import Path

# Adicionar o diretório backend ao path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from app.config.database import Base, engine
from app.config.logging_config import get_logger
from app.models import (
    Usuario,
    Formulario,
    ArquivoPdf,
    Escola,
    UnidadeEscolar,
    Turma,
    DistribuicaoMaterial,
    EspecificacaoForm,
    BremenPedido,
    BremenItem,
    BremenComponente
)

logger = get_logger(__name__)

def init_database():
    """
    Cria todas as tabelas no banco de dados
    """
    try:
        logger.info("=" * 80)
        logger.info("Iniciando criação das tabelas no banco de dados...")
        logger.info("=" * 80)
        
        # Criar todas as tabelas
        Base.metadata.create_all(bind=engine)
        
        logger.info("✅ Tabelas criadas com sucesso!")
        logger.info("=" * 80)
        logger.info("Tabelas criadas:")
        for table_name in Base.metadata.tables.keys():
            logger.info(f"  - {table_name}")
        logger.info("=" * 80)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erro ao criar tabelas: {str(e)}", exc_info=True)
        return False

def drop_all_tables():
    """
    Remove todas as tabelas do banco de dados
    CUIDADO: Esta operação é destrutiva!
    """
    try:
        logger.warning("=" * 80)
        logger.warning("ATENÇÃO: Removendo todas as tabelas do banco de dados...")
        logger.warning("=" * 80)
        
        Base.metadata.drop_all(bind=engine)
        
        logger.info("✅ Tabelas removidas com sucesso!")
        logger.info("=" * 80)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erro ao remover tabelas: {str(e)}", exc_info=True)
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Gerenciar tabelas do banco de dados')
    parser.add_argument(
        '--drop',
        action='store_true',
        help='Remove todas as tabelas antes de criar (CUIDADO: operação destrutiva!)'
    )
    
    args = parser.parse_args()
    
    if args.drop:
        response = input("⚠️  ATENÇÃO: Isso irá APAGAR TODAS as tabelas e dados! Confirma? (sim/não): ")
        if response.lower() == 'sim':
            if drop_all_tables():
                print("\n")
                init_database()
        else:
            print("❌ Operação cancelada.")
    else:
        init_database()
