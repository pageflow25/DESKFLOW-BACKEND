"""Regras de negócio, um subpacote por domínio (`clientes/`, `pcp/`).

Os controllers chamam os serviços; os serviços usam `repositorios/` (banco),
`integracoes/` (ERP) e `utils/`, e nunca importam um controller.
"""
