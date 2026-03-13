# 📊 ARQUITETURA DO MÓDULO "ACOMPANHAMENTO DE DISPAROS"

## Visão Geral
O módulo de Acompanhamento de Disparos é uma feature de **auditoria e monitoramento** dos envios de orçamentos agrupados em lotes. Ele permite visualizar toda a cascata de processamento desde o **Lote → Orçamento → Distribuição → Ordens de Produção (OPs)** com timeline de eventos.

---

## 🗄️ TABELAS DO BANCO DE DADOS

| Tabela | Propósito | Campos-Chave |
|--------|-----------|--------------|
| **distribuicao_materiais** | Matriz física de distribuição de materiais por unidade escolar | `id`, `grupo_lote_id`, `quantidade`, `status_distribuicao`, `id_orcamento` |
| **orcamento_api** | Cache da FASE 01 (resposta da API Bremen de orçamento) | `distribuicao_material_id`, `id_orcamento`, `id_item`, `itens` (JSON) |
| **aprovacao_api** | Cache da FASE 02 (resposta da API Bremen de aprovação) | `distribuicao_material_id`, `id_orcamento`, `id_ops` (Ordem de Produção) |
| **historico_processamento** | **Log temporal** de eventos/transições por distribuição | `distribuicao_material_id`, `grupo_lote_id`, `status_anterior_id`, `status_novo_id`, `sucesso`, `data_evento` |
| **status_deskflow_pedido** | Tabela de lookup com status possíveis | `id`, `codigo` (ex: "ENVIADO", "ERRO") |

---

## 🏗️ ESTRUTURA DE CAMADAS

### 1️⃣ FRONTEND (React + Vite)

**Arquivo:** `src/renderer/src/pages/DisparoMonitor.jsx`

**Funcionalidades:**
- Listagem paginada de lotes (10 por página)
- Accordions expansíveis: Lote → Orçamento → Distribuição
- Timeline de eventos com status (✓ Sucesso / ✗ Erro)
- Exibição de OPs associadas
- Filtros visuais por status

**Serviço utilizado:**
```javascript
orcamentoService.getLotesDisparo(limit, offset)
```
- Endpoint: `GET /api/orcamento/lotes/disparos?limit=10&offset=0`

---

### 2️⃣ BACKEND (FastAPI + SQLAlchemy)

#### Router: `app/routers/orcamento.py` (linhas 117-139)

```python
@router.get("/lotes/disparos", response_model=LoteDisparoListResponse)
async def listar_lotes_disparo(
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)  # ← Requer role ADMIN
)
```

**Requerimentos:**
- Token JWT + Role `admin`
- Paginação via `limit` e `offset`

---

#### Controller: `OrcamentoController.listar_lotes_disparo()`

A função executa **3 queries SQL complexas com JOINs**:

**Query 1: Resumo do Lote**
```sql
SELECT grupo_lote_id, MIN(data_evento), COUNT(distribuicoes)
FROM historico_processamento
GROUP BY grupo_lote_id
ORDER BY grupo_lote_id DESC
```
- Retorna: `total_pedidos`, `total_sucesso`, `total_erro`

**Query 2: Distribuições com Informações Completas**
```sql
WITH ultimos AS (
  SELECT DISTINCT ON (distribuicao_material_id)
    distribuicao_material_id, status, sucesso, mensagem
  FROM historico_processamento
  ORDER BY distribuicao_material_id, data_evento DESC
)
SELECT
  -- JOINs com distribuicao_materiais, escolas, unidades, arquivo_pdfs
  -- JOINs com orcamento_api para recuperar id_orcamento
  -- LEFT JOINs para status_deskflow_pedido
```

**Query 3: OPs por Distribuição**
```sql
SELECT distribuicao_material_id, id_ops, pedidos
FROM aprovacao_api
WHERE id_ops IS NOT NULL
  AND distribuicao_material_id IN (...)
```

---

### 3️⃣ SCHEMAS (Pydantic)

**Arquivo:** `app/schemas/orcamento.py` (linhas 128-174)

**Hierarquia de Serialização:**

```
LoteDisparoListResponse
├── lotes: List[LoteDisparoResumo]
│   ├── grupo_lote_id: int
│   ├── data_envio: Optional[str]
│   ├── total_pedidos: int
│   ├── total_sucesso: int
│   ├── total_erro: int
│   ├── escolas: List[str]
│   ├── destinos: List[str]  ← Nomes de unidades
│   └── orcamentos: List[LoteDisparoOrcamento]
│       ├── id_orcamento: Optional[int]
│       └── distribuicoes: List[LoteDisparoDistribuicao]
│           ├── distribuicao_material_id: int
│           ├── escola_nome: str
│           ├── unidade_nome: str
│           ├── material_descricao: str
│           ├── arquivo_nome: str
│           ├── quantidade: int
│           ├── sucesso: bool
│           ├── ops: List[LoteDisparoOP]
│           │   ├── id_ops: int
│           │   └── pedido: {id, serie, empresa}
│           └── eventos: List[LoteDisparoEvento]
│               ├── status: str
│               ├── sucesso: bool
│               ├── mensagem: Optional[str]
│               └── data_evento: str
└── total_geral: int
```

---

## 🔄 FLUXO DE DADOS COMPLETO

```
1. Frontend: DisparoMonitor.jsx monta
   ↓
2. Carrega getLotesDisparo(page=1, limit=10)
   ↓
3. Backend Router: GET /api/orcamento/lotes/disparos?limit=10&offset=0
   ↓
4. Controller Query 1: Agrupa por grupo_lote_id
   ↓
5. Para cada Lote:
   - Query 2: Obtém distribuições com status
   - Query 3: Obtém OPs relacionadas
   - Query 4: Obtém timeline de eventos
   ↓
6. Serializa para LoteDisparoListResponse
   ↓
7. Frontend renderiza com accordions aninhados
   ↓
8. User expande: Lote → Orçamento → Distribuição → Timeline
```

---

## 🎯 FUNÇÕES-CHAVE PARA CRIAR A FEATURE

| Função | Arquivo | Responsabilidade |
|--------|---------|------------------|
| `listar_lotes_disparo()` | OrcamentoController | Orquestra 4 queries SQL para construir hierarquia Lote→Orc→Dist |
| `getLotesDisparo()` | api.js | Chamada HTTP ao backend |
| `DisparoMonitor()` | DisparoMonitor.jsx | UI com accordions e timeline |
| Schemas | orcamento.py | Define estrutura de resposta |
| Query JOINs | orcamento_controller.py (linhas 95-175) | Busca dados de múltiplas tabelas |

---

## 📈 ESTRUTURA DE DADOS NA TIMELINE

Cada evento na timeline vem de **historico_processamento**:

```python
LoteDisparoEvento(
    status="enviado_para_bremen",  # Vem de status_deskflow_pedido.codigo
    sucesso=True,
    mensagem="Resposta recebida com sucesso",
    data_evento="2025-03-12T14:30:00.000-03:00"
)
```

Após cada transição de fase (orçamento → aprovação → produção), um novo registro é inserido, criando a timeline visual.

---

## ✅ RESUMO TÉCNICO

| Aspecto | Detalhe |
|---------|---------|
| **Modelo de Dados** | Star-schema expandido (Lote → Distribuição) |
| **Paginação** | Offset-based (limit/offset) |
| **Segurança** | JWT + Verificação de role ADMIN |
| **Cache de API** | orcamento_api + aprovacao_api armazenam respostas |
| **Auditoria** | historico_processamento fornece timeline completa |
| **Performance** | índices em grupo_lote_id, distribuicao_material_id, data_evento |
