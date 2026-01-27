# 🏭 DeskFlow - Sistema de Orçamentos com Distribuição

## 📋 Fluxo Completo do Sistema

### 🎯 **Objetivo**
Implementar um sistema automatizado para geração e aprovação de orçamentos com distribuição direta (sem faturamento), integrando com a API Bremen.

---

## 🔄 **FLUXO DETALHADO DE PROCESSAMENTO**

### **📊 FASE 01 - GERAR ORÇAMENTO VIA API**

#### **1.1 - Entrada de Parâmetros**
Quando o usuário clica no botão "Gerar Orçamento" no frontend:
```json
{
  "escola_id": 1,
  "ids_produtos": [101, 102, 103],
  "datas_saida": ["2026-01-30", "2026-02-15"],
  "divisoes_logistica": ["Norte", "Sul"],
  "dias_uteis_filtro": [1, 2, 3, 4, 5]
}
```

#### **1.2 - Processamento Local**
1. **Execução da Query SQL** (`query_orcamento.sql`)
   - Filtra distribuições por escola e produtos
   - Monta estrutura do orçamento com componentes e especificações
   - Agrupa por unidade escolar

2. **Geração do Payload para API Bremen**
```json
{
  "identifier": "PageFlow",
  "data": {
    "id_cliente": null,
    "id_vendedor": 2285,
    "id_forma_pagamento": "11",
    "itens": [
      {
        "id_produto": 101,
        "descricao": "LIVRO DO PROFESSOR 8OANO LIVRO1",
        "quantidade": 22,
        "usar_listapreco": 1,
        "manter_estrutura_mod_produto": 1,
        "componentes": [...],
        "perguntas_gerais": [...]
      }
    ]
  }
}
```

#### **1.3 - Requisição para API Bremen**
- **URL**: `{BREMEN_API_URL}/api/v1/orcamento`
- **Method**: POST
- **Headers**: `Authorization: {BREMEN_API_TOKEN}`

#### **1.4 - Salvamento na Tabela `orcamento_api`**
```sql
INSERT INTO orcamento_api (
    distribuicao_material_id,
    id_orcamento, 
    itens,
    resposta_api
) VALUES (
    18014,
    1716,
    '[{"id": 24516, "descricao": "LIVRO DO PROFESSOR...", "quantidade": 22}]',
    '{"identifier": "PageFlow", "data": {...}}'
);
```

#### **1.5 - Atualização de Status**
- **Status**: `orcamento_gerado`
- **Histórico**: Salvo em `historico_processamento`

---

### **✅ FASE 02 - APROVAÇÃO VIA API (OPCIONAL)**

#### **2.1 - Montagem da Aprovação**
Usando dados da tabela `orcamento_api`:
```json
{
  "identifier": "PageFlow",
  "data": {
    "id_orcamento": 1716,
    "gerar_op": true,
    "itens": [
      {
        "id": 24516,
        "data_entrega": "2026-01-15T12:00:00.000-03:00"
      },
      {
        "id": 24517, 
        "data_entrega": "2026-01-15T12:00:00.000-03:00"
      }
    ]
  }
}
```

#### **2.2 - Requisição para API Bremen**
- **URL**: `{BREMEN_API_URL}/api/v1/proposta/aprovar`
- **Method**: POST
- **Headers**: `Authorization: {BREMEN_API_TOKEN}`

#### **2.3 - Salvamento na Tabela `aprovacao_api`**
```sql
INSERT INTO aprovacao_api (
    distribuicao_material_id,
    id_orcamento,
    id_ops,
    pedidos,
    resposta_api
) VALUES (
    18014,
    1716,
    2345,
    '[{"id_pedido": 789, "status": "aprovado"}]',
    '{"identifier": "PageFlow", "data": {...}}'
);
```

#### **2.4 - Atualização de Status Final**
- **Status**: `orcamento_aprovado`
- **Resultado**: OPs (Ordens de Produção) geradas automaticamente

---

## 🗃️ **ESTRUTURA DO BANCO DE DADOS**

### **Tabela: `orcamento_api`**
| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | INT | Chave primária |
| `distribuicao_material_id` | INT | FK para distribuição |
| `id_orcamento` | INT | ID retornado pela API Bremen |
| `itens` | JSONB | Array de itens do orçamento |
| `resposta_api` | JSONB | Resposta completa da API |
| `criado_em` | TIMESTAMP | Data de criação |

### **Tabela: `aprovacao_api`**
| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | INT | Chave primária |
| `distribuicao_material_id` | INT | FK para distribuição |
| `id_orcamento` | INT | ID do orçamento aprovado |
| `id_ops` | INT | ID das OPs geradas |
| `pedidos` | JSONB | Array de pedidos gerados |
| `resposta_api` | JSONB | Resposta completa da API |
| `criado_em` | TIMESTAMP | Data de criação |

### **Tabela: `historico_processamento`**
| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | INT | Chave primária |
| `distribuicao_material_id` | INT | FK para distribuição |
| `status_anterior` | VARCHAR | Status anterior |
| `status_novo` | VARCHAR | Novo status |
| `mensagem` | TEXT | Mensagem do evento |
| `sucesso` | BOOLEAN | Se foi bem-sucedido |
| `data_evento` | TIMESTAMP | Data do evento |

---

## 🚀 **ENDPOINTS DA API**

### **Principal**
```http
POST /api/orcamento/processar
```
**Descrição**: Processa orçamento completo com seleção de fluxo

**Request Body**:
```json
{
  "tipo_fluxo": "com_distribuicao_sem_faturamento",
  "escola_id": 1,
  "ids_produtos": [101, 102, 103],
  "datas_saida": ["2026-01-30"],
  "divisoes_logistica": ["Norte"],
  "dias_uteis_filtro": [1, 2, 3, 4, 5],
  "aprovar_automaticamente": true,
  "data_entrega": "2026-01-15T12:00:00.000-03:00"
}
```

**Response**:
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

### **Auxiliares**
- `POST /api/orcamento/aprovar/{id_orcamento}` - Aprovação manual
- `GET /api/orcamento/status/{escola_id}` - Consultar status
- `GET /api/orcamento/arquivos/listar` - Listar arquivos

---

## ⚙️ **CONFIGURAÇÃO**

### **Variáveis de Ambiente (.env)**
```bash
# API Bremen (ÚNICA NECESSÁRIA)
BREMEN_API_URL=http://192.168.1.215:9001
BREMEN_API_TOKEN=Bearer ZXlKMGVYQWlPaUpLVjFRaUxDSmhiR2NpT2lKSVV6STFOaUo5...
API_TIMEOUT=30

# Banco de Dados
DATABASE_URL=postgresql://user:pass@localhost/deskflow

# JWT
SECRET_KEY=sua_chave_secreta
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

---

## 💻 **COMO USAR**

### **1. Via Interface Web**
1. Abra `demo_frontend_orcamento.html` no navegador
2. Preencha os dados da escola e produtos
3. Clique "Gerar Orçamento"
4. Selecione "Com Distribuição (Sem Faturamento)"
5. Marque "Aprovar automaticamente" se desejar
6. Defina a data de entrega
7. Confirme e processe

### **2. Via API REST**
```python
import requests

data = {
    "tipo_fluxo": "com_distribuicao_sem_faturamento",
    "escola_id": 1,
    "ids_produtos": [101, 102],
    "datas_saida": ["2026-01-30"],
    "aprovar_automaticamente": True,
    "data_entrega": "2026-01-15T12:00:00.000-03:00"
}

response = requests.post(
    "http://localhost:8000/api/orcamento/processar",
    json=data,
    headers={"Authorization": "Bearer seu_token"}
)

result = response.json()
print(f"Processados: {result['enviados']} de {result['total']}")
```

---

## 📊 **MONITORAMENTO E STATUS**

### **Status de Processamento**
- `pendente` → Distribuição criada
- `orcamento_gerado` → FASE 01 concluída ✅
- `orcamento_aprovado` → FASE 02 concluída ✅
- `erro_orcamento` → Erro na FASE 01 ❌
- `erro_aprovacao` → Erro na FASE 02 ❌

### **Consulta de Status**
```http
GET /api/orcamento/status/1
```

**Response**:
```json
{
  "escola_id": 1,
  "total_distribuicoes": 5,
  "distribuicoes": [
    {
      "distribuicao_id": 18014,
      "unidade_nome": "Escola Central",
      "item_nome": "Livro Professor 8º Ano",
      "quantidade": 22,
      "status_codigo": "orcamento_aprovado",
      "id_orcamento": 1716,
      "id_ops": 2345,
      "tem_orcamento": true,
      "foi_aprovado": true
    }
  ]
}
```

---

## 🔧 **ARQUITETURA DO SISTEMA**

### **Camadas da Aplicação**

1. **Router** (`orcamento.py`)
   - Endpoints REST
   - Validação de autenticação
   - Tratamento de erros HTTP

2. **Controller** (`orcamento_controller.py`)
   - Coordenação do fluxo
   - Orquestração entre services
   - Lógica de processamento

3. **Services**
   - `orcamento_service.py` - Lógica de negócio
   - `orcamento_api_service.py` - Integração com API Bremen

4. **Models**
   - `orcamento_api.py` - Modelo da FASE 01
   - `aprovacao_api.py` - Modelo da FASE 02
   - `historico_processamento.py` - Log de eventos

5. **Schemas** (`orcamento.py`)
   - Validação de dados de entrada
   - Serialização de responses

---

## 🛡️ **SEGURANÇA E BOAS PRÁTICAS**

- ✅ **Autenticação JWT** obrigatória
- ✅ **Validação de dados** em todas as camadas
- ✅ **Logs detalhados** para auditoria
- ✅ **Transações de banco** com rollback
- ✅ **Timeout configurável** para APIs externas
- ✅ **Rate limiting** recomendado

---

## 🧪 **TESTING E DEBUGGING**

### **Teste Rápido**
1. Use o `demo_frontend_orcamento.html`
2. Configure `escola_id = 1` e `ids_produtos = [101]`
3. Execute o fluxo completo
4. Verifique os logs da aplicação

### **Debugging**
```python
# Ver logs em tempo real
tail -f logs/application.log

# Consultar tabelas no banco
SELECT * FROM orcamento_api WHERE distribuicao_material_id = 18014;
SELECT * FROM aprovacao_api WHERE distribuicao_material_id = 18014;
SELECT * FROM historico_processamento WHERE distribuicao_material_id = 18014;
```

---

**Sistema otimizado e pronto para produção!** 🚀