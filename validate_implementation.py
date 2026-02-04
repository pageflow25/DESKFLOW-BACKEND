#!/usr/bin/env python3
"""
Script de validação da implementação do novo fluxo de orçamento
"""

import sys
import os

# Adicionar o diretório raiz ao path para permitir imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Testa se todos os imports estão funcionando"""
    try:
        print("Testing imports...")
        
        # Test schema imports
        from app.schemas.orcamento import (
            EnviarOrcamentoRequest, 
            ProcessamentoResultado, 
            OrcamentoData, 
            ItemOrcamento
        )
        print("✓ Schema imports successful")
        
        # Test model imports
        from app.models.distribuicao_material import DistribuicaoMaterial, StatusDistribuicao
        from app.models.orcamento_api import OrcamentoAPI
        from app.models.aprovacao_api import AprovacaoAPI
        print("✓ Model imports successful")
        
        # Test service imports
        from app.services.orcamento_service_new import OrcamentoService
        print("✓ Service imports successful")
        
        # Test controller imports
        from app.controllers.orcamento_controller import OrcamentoController
        print("✓ Controller imports successful")
        
        # Test config imports
        from app.config.settings import get_settings
        settings = get_settings()
        print(f"✓ Settings loaded: {settings.APP_NAME if hasattr(settings, 'APP_NAME') else 'No APP_NAME'}")
        
        return True
        
    except Exception as e:
        print(f"✗ Import error: {e}")
        return False

def test_service_methods():
    """Testa se os métodos do serviço estão definidos"""
    try:
        print("\nTesting service methods...")
        
        from app.services.orcamento_service_new import OrcamentoService
        
        # Check if methods exist
        methods = [
            'buscar_distribuicoes_para_orcamento',
            'preparar_dados_orcamento', 
            'enviar_orcamento_api_externa',
            'salvar_resposta_orcamento',
            'aprovar_orcamento_automatico',
            'salvar_resposta_aprovacao',
            'atualizar_status_distribuicao',
            'processar_workflow_completo'
        ]
        
        for method in methods:
            if hasattr(OrcamentoService, method):
                print(f"✓ Method {method} exists")
            else:
                print(f"✗ Method {method} missing")
                return False
        
        return True
        
    except Exception as e:
        print(f"✗ Service method test error: {e}")
        return False

def test_schema_validation():
    """Testa se os schemas estão corretamente definidos"""
    try:
        print("\nTesting schema validation...")
        
        from app.schemas.orcamento import EnviarOrcamentoRequest, ProcessamentoResultado
        from datetime import date
        
        # Test request schema
        request_data = {
            "escola_id": 1,
            "ids_produtos": [100, 200],
            "datas_saida": [date.today()],
            "aprovar_automaticamente": True
        }
        
        request = EnviarOrcamentoRequest(**request_data)
        print(f"✓ Request schema validation successful: escola_id={request.escola_id}")
        
        # Test response schema  
        resultado_data = {
            "total": 5,
            "enviados": 1, 
            "aprovados": 1,
            "salvos": 5,
            "erros": [],
            "detalhes": []
        }
        
        resultado = ProcessamentoResultado(**resultado_data)
        print(f"✓ Response schema validation successful: total={resultado.total}")
        
        return True
        
    except Exception as e:
        print(f"✗ Schema validation error: {e}")
        return False

def main():
    """Função principal de validação"""
    print("=== Validação da Implementação do Novo Fluxo de Orçamento ===\n")
    
    success = True
    
    # Test imports
    if not test_imports():
        success = False
    
    # Test service methods
    if not test_service_methods():
        success = False
    
    # Test schema validation
    if not test_schema_validation():
        success = False
    
    print(f"\n=== Resultado da Validação ===")
    if success:
        print("✓ Todos os testes passaram! A implementação está pronta.")
    else:
        print("✗ Alguns testes falharam. Verifique os erros acima.")
    
    return success

if __name__ == "__main__":
    main()