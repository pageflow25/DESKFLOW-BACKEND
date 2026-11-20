"""Handlers do dominio CLIENTES (espelho de bremen_clientes a partir do ERP).

Um modulo por `tipo_codigo` do registry. Cada um conhece o formato do payload, o
endpoint do destino e o formato do resultado que o projetor do PageFlow le — e
nada mais: o motor da fila nao importa nada daqui, a dependencia e so pelo
registry.
"""
