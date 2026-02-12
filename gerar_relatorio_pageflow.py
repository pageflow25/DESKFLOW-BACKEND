"""
Script para gerar relatório de orçamento PageFlow
"""
import json
from datetime import datetime
from pathlib import Path


def gerar_relatorio(arquivo_json: str, arquivo_saida: str = None):
    """
    Gera um relatório detalhado a partir do arquivo JSON PageFlow
    
    Args:
        arquivo_json: Caminho do arquivo JSON
        arquivo_saida: Caminho do arquivo de saída (opcional). Se None, imprime no console
    """
    
    # Ler o arquivo JSON
    with open(arquivo_json, 'r', encoding='utf-8') as f:
        dados = json.load(f)
    
    # Preparar o relatório
    linhas = []
    linhas.append("=" * 80)
    linhas.append("RELATÓRIO DE ORÇAMENTO PAGEFLOW")
    linhas.append("=" * 80)
    linhas.append(f"Data de Geração: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    linhas.append("")
    
    # Informações gerais
    linhas.append("-" * 80)
    linhas.append("INFORMAÇÕES GERAIS")
    linhas.append("-" * 80)
    data = dados.get('data', {})
    linhas.append(f"Identificador: {dados.get('identifier', 'N/A')}")
    linhas.append(f"ID Escola: {data.get('id_escola', 'N/A')}")
    linhas.append(f"ID Cliente: {data.get('id_cliente', 'N/A')}")
    linhas.append(f"ID Vendedor: {data.get('id_vendedor', 'N/A')}")
    linhas.append(f"ID Forma de Pagamento: {data.get('id_forma_pagamento', 'N/A')}")
    linhas.append("")
    
    # Análise dos itens
    itens = data.get('itens', [])
    itens_validos = [item for item in itens if item and item.get('id_produto')]
    itens_vazios = len(itens) - len(itens_validos)
    
    linhas.append("-" * 80)
    linhas.append("RESUMO DE ITENS")
    linhas.append("-" * 80)
    linhas.append(f"Total de Itens: {len(itens)}")
    linhas.append(f"Itens Válidos: {len(itens_validos)}")
    linhas.append(f"Itens Vazios: {itens_vazios}")
    linhas.append("")
    
    # Estatísticas gerais
    quantidade_total = sum(item.get('quantidade', 0) for item in itens_validos)
    produtos_unicos = len(set(item.get('id_produto') for item in itens_validos))
    
    linhas.append(f"Quantidade Total de Produtos: {quantidade_total}")
    linhas.append(f"Produtos Únicos: {produtos_unicos}")
    linhas.append("")
    
    # Agrupamento por produto
    produtos_agrupados = {}
    for item in itens_validos:
        id_produto = item.get('id_produto')
        quantidade = item.get('quantidade', 0)
        
        if id_produto in produtos_agrupados:
            produtos_agrupados[id_produto]['quantidade'] += quantidade
            produtos_agrupados[id_produto]['ocorrencias'] += 1
        else:
            produtos_agrupados[id_produto] = {
                'quantidade': quantidade,
                'ocorrencias': 1
            }
    
    linhas.append("-" * 80)
    linhas.append("RESUMO POR ID DE PRODUTO")
    linhas.append("-" * 80)
    linhas.append(f"{'ID Produto':<15} {'Quantidade Total':<20} {'Ocorrências':<15}")
    linhas.append("-" * 80)
    
    for id_produto, info in sorted(produtos_agrupados.items()):
        linhas.append(f"{id_produto:<15} {info['quantidade']:<20} {info['ocorrencias']:<15}")
    
    linhas.append("")
    
    # Detalhamento de cada item válido
    linhas.append("-" * 80)
    linhas.append("DETALHAMENTO DOS ITENS")
    linhas.append("-" * 80)
    linhas.append("")
    
    for idx, item in enumerate(itens_validos, 1):
        linhas.append(f"ITEM #{idx}")
        linhas.append("-" * 40)
        linhas.append(f"  ID Produto: {item.get('id_produto', 'N/A')}")
        linhas.append(f"  Descrição: {item.get('descricao', 'N/A')}")
        linhas.append(f"  Quantidade: {item.get('quantidade', 'N/A')}")
        linhas.append(f"  Usar Lista de Preço: {'Sim' if item.get('usar_listapreco') == 1 else 'Não'}")
        linhas.append(f"  Manter Estrutura: {'Sim' if item.get('manter_estrutura_mod_produto') == 1 else 'Não'}")
        
        # IDs de distribuição
        ids_dist = item.get('ids_distribuicao', [])
        linhas.append(f"  IDs Distribuição: {len(ids_dist)} item(s)")
        if ids_dist:
            linhas.append(f"    Primeiro: {ids_dist[0]}, Último: {ids_dist[-1]}")
        
        # Componentes
        componentes = item.get('componentes', [])
        linhas.append(f"  Componentes: {len(componentes)} item(s)")
        
        # Perguntas gerais
        perguntas = item.get('perguntas_gerais', [])
        linhas.append(f"  Perguntas Gerais: {len(perguntas)} item(s)")
        
        linhas.append("")
    
    # Rodapé
    linhas.append("=" * 80)
    linhas.append("FIM DO RELATÓRIO")
    linhas.append("=" * 80)
    
    # Gerar o texto do relatório
    relatorio = "\n".join(linhas)
    
    # Saída
    if arquivo_saida:
        with open(arquivo_saida, 'w', encoding='utf-8') as f:
            f.write(relatorio)
        print(f"✓ Relatório gerado com sucesso em: {arquivo_saida}")
    else:
        print(relatorio)
    
    return relatorio


def main():
    """Função principal"""
    import sys
    
    # Verificar argumentos
    if len(sys.argv) < 2:
        print("Uso: python gerar_relatorio_pageflow.py <arquivo.json> [arquivo_saida.txt]")
        print("\nExemplos:")
        print("  python gerar_relatorio_pageflow.py .json")
        print("  python gerar_relatorio_pageflow.py .json relatorio.txt")
        sys.exit(1)
    
    arquivo_json = sys.argv[1]
    arquivo_saida = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Verificar se o arquivo existe
    if not Path(arquivo_json).exists():
        print(f"✗ Erro: Arquivo '{arquivo_json}' não encontrado!")
        sys.exit(1)
    
    # Gerar relatório
    try:
        gerar_relatorio(arquivo_json, arquivo_saida)
    except Exception as e:
        print(f"✗ Erro ao gerar relatório: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
