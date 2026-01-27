# 🏭 DeskFlow - Sistema de Orçamentos com Distribuição

## 📋 Visão Geral

Este sistema implementa um fluxo completo de geração e aprovação de orçamentos com distribuição automática, seguindo o padrão DeskFlow. O sistema permite processar orçamentos em duas fases:

### 🔄 FASE 01 - GERAR ORÇAMENTO VIA API (COM DISTRIBUIÇÃO)
- Recebe parâmetros: escola_id, ids_produtos, datas_saida, divisao_logistica, dias_uteis
- Faz requisição para `/api/v1/orcamento`
- Salva o retorno na tabela `orcamento_api`
- Atualiza status no `historico_processamento`

### 🔄 FASE 02 - APROVAÇÃO VIA API (COM DISTRIBUIÇÃO)
- Faz requisição para `/api/v1/proposta/aprovar`
- Usa dados da tabela `orcamento_api` para montar a aprovação
- Gera OPs (Ordens de Produção) automaticamente
- Salva o retorno na tabela `aprovacao_api`
- Atualiza status no `historico_processamento`

## 🚀 Como Usar

### 1. Via API REST

#### Endpoint Principal
```http
POST /api/orcamento/processar
```

#### Payload de Exemplo
```json
{
  "tipo_fluxo": "com_distribuicao_sem_faturamento",
  "escola_id": 1,
  "ids_produtos": [101, 102, 103],
  "datas_saida": ["2026-01-30", "2026-02-15"],
  "divisoes_logistica": ["Norte", "Sul"],
  "dias_uteis_filtro": [1, 2, 3, 4, 5],
  "aprovar_automaticamente": true,
  "data_entrega": "2026-01-15T12:00:00.000-03:00"
}
```

#### Response de Exemplo
```json
{
  "total": 5,
  "enviados": 5,
  "aprovados": 5,
  "salvos": 5,
  "erros": [],
  "detalhes": [
    {
      "fase": "01_orcamento",
      "distribuicao_id": 18014,
      "id_orcamento": 1716,
      "itens_count": 1,
      "status": "sucesso"
    },
    {
      "fase": "02_aprovacao", 
      "distribuicao_id": 18014,
      "id_orcamento": 1716,
      "id_ops": 2345,
      "pedidos_count": 1,
      "status": "sucesso"
    }
  ]
}
```

### 2. Via Interface Web

Abra o arquivo `demo_frontend_orcamento.html` no navegador para usar a interface visual que inclui:

- ✅ Formulário para inserir dados da escola e produtos
- 🎯 Modal de seleção do tipo de fluxo
- ⚡ Opção de aprovação automática
- 📊 Visualização detalhada dos resultados

## 🗃️ Estrutura do Banco de Dados

### Tabela: `orcamento_api`
Armazena retornos da API de geração de orçamento (FASE 01)
```sql
|id|distribuicao_material_id|id_orcamento|itens|resposta_api|criado_em|
|--|------------------------|------------|-----|------------|---------|
|9 |18014                   |1716        |[...]|{...}       |...      |
```

### Tabela: `aprovacao_api` 
Armazena retornos da API de aprovação (FASE 02)
```sql
|id|distribuicao_material_id|id_orcamento|id_ops|pedidos|resposta_api|criado_em|
|--|------------------------|------------|------|-------|------------|---------|
|5 |18014                   |1716        |2345  |[...]  |{...}       |...      |
```

### Tabela: `historico_processamento`
Log de eventos e mudanças de status
```sql
|id|distribuicao_material_id|status_anterior|status_novo|mensagem|sucesso|data_evento|
|--|------------------------|---------------|-----------|--------|-------|-----------|
|1 |18014                   |pendente       |orcamento_gerado|...|true   |...        |
|2 |18014                   |orcamento_gerado|orcamento_aprovado|...|true|...       |
```

## 🛠️ Endpoints Disponíveis

### Principais
- `POST /api/orcamento/processar` - Processamento completo com seleção de fluxo
- `POST /api/orcamento/gerar` - Geração de orçamento (modo legado)

### Auxiliares
- `POST /api/orcamento/aprovar/{id_orcamento}` - Aprovação manual
- `GET /api/orcamento/status/{escola_id}` - Consultar status dos orçamentos
- `GET /api/orcamento/arquivos/listar` - Listar arquivos de orçamento
- `GET /api/orcamento/arquivos/download/{nome_arquivo}` - Download de arquivo
- `DELETE /api/orcamento/arquivos/deletar/{nome_arquivo}` - Deletar arquivo

## ⚙️ Configuração

### Variáveis de Ambiente (.env)
```bash
# API DeskFlow
DESKFLOW_API_BASE_URL=https://api.deskflow.com.br
DESKFLOW_API_TOKEN=seu_token_aqui
API_TIMEOUT=30

# Banco de Dados
DATABASE_URL=postgresql://user:pass@localhost/deskflow

# JWT
SECRET_KEY=sua_chave_secreta
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

### Dependências Python
```bash
pip install fastapi uvicorn sqlalchemy psycopg2-binary httpx pydantic-settings python-jose passlib bcrypt
```

## 🔧 Exemplo de Integração

### Python/Requests
```python
import requests

# Dados do orçamento
data = {
    "tipo_fluxo": "com_distribuicao_sem_faturamento",
    "escola_id": 1,
    "ids_produtos": [101, 102],
    "datas_saida": ["2026-01-30"],
    "aprovar_automaticamente": True,
    "data_entrega": "2026-01-15T12:00:00.000-03:00"
}

# Fazer requisição
response = requests.post(
    "http://localhost:8000/api/orcamento/processar",
    json=data,
    headers={"Authorization": "Bearer seu_token"}
)

result = response.json()
print(f"Processados: {result['enviados']} de {result['total']}")
```

### JavaScript/Fetch
```javascript
const processarOrcamento = async () => {
    const data = {
        tipo_fluxo: "com_distribuicao_sem_faturamento",
        escola_id: 1,
        ids_produtos: [101, 102],
        datas_saida: ["2026-01-30"],
        aprovar_automaticamente: true,
        data_entrega: "2026-01-15T12:00:00.000-03:00"
    };

    const response = await fetch('/api/orcamento/processar', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token
        },
        body: JSON.stringify(data)
    });

    const result = await response.json();
    console.log('Resultado:', result);
};
```

## 📝 Status de Processamento

O sistema controla os seguintes status:

- `pendente` - Distribuição criada, aguardando processamento
- `orcamento_gerado` - FASE 01 concluída com sucesso
- `orcamento_aprovado` - FASE 02 concluída com sucesso  
- `erro_orcamento` - Erro na FASE 01
- `erro_aprovacao` - Erro na FASE 02

## 🚦 Monitoramento

### Consultar Status de uma Escola
```http
GET /api/orcamento/status/1
```

### Logs da Aplicação
Os logs são gerados automaticamente e incluem:
- Início/fim de cada fase
- Detalhes dos IDs gerados
- Erros e exceções
- Performance das APIs externas

## 🔐 Segurança

- ✅ Autenticação JWT obrigatória
- ✅ Verificação de permissões de admin
- ✅ Validação de dados de entrada
- ✅ Sanitização de parâmetros SQL
- ✅ Rate limiting (recomendado configurar)

## 🧪 Testes

Para testar o sistema:

1. **Use o demo HTML**: Abra `demo_frontend_orcamento.html`
2. **API direta**: Use Postman ou curl
3. **Status**: Verifique `/api/orcamento/status/{escola_id}`

## 📞 Suporte

Em caso de problemas:

1. Verifique os logs da aplicação
2. Consulte a tabela `historico_processamento`
3. Teste a conectividade com as APIs externas
4. Valide as configurações no `.env`

---

**Desenvolvido para o sistema DeskFlow** 🚀