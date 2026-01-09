# API Documentation - DESKFLOW

## Base URL
```
http://localhost:8000
```

## Autenticação

Todos os endpoints protegidos requerem um token JWT no header:
```
Authorization: Bearer <token>
```

### POST /api/auth/login

Autentica um usuário administrador e retorna um token JWT.

**Request:**
```json
{
  "username": "admin",
  "password": "senha123"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user_id": 1,
  "user_name": "Administrador",
  "roles": "admin"
}
```

**Errors:**
- `401 Unauthorized` - Credenciais inválidas
- `403 Forbidden` - Usuário não é admin

---

### POST /api/auth/validate

Valida um token JWT.

**Headers:**
```
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
{
  "valid": true,
  "user_data": {
    "user_id": "1",
    "username": "admin",
    "roles": "admin"
  }
}
```

**Errors:**
- `401 Unauthorized` - Token inválido ou expirado
- `403 Forbidden` - Sem permissão PCP

---

## Dashboard

### GET /api/dashboard/escolas

Retorna lista de escolas com contagem de pedidos.

**Requer:** Admin authentication

**Response (200 OK):**
```json
{
  "escolas": [
    {
      "escola_id": 1,
      "nome_escola": "Escola Exemplo",
      "codigo_escola": "ESC001",
      "total_pedidos": 15
    },
    {
      "escola_id": 2,
      "nome_escola": "Escola Teste",
      "codigo_escola": "ESC002",
      "total_pedidos": 8
    }
  ],
  "total_escolas": 2
}
```

**Errors:**
- `401 Unauthorized` - Token inválido
- `403 Forbidden` - Não é admin
- `500 Internal Server Error` - Erro no servidor

---

## Pedidos

### GET /api/pedidos/escola/{escola_id}/cascata

Retorna pedidos da escola em estrutura hierárquica (Divisões → Produtos → Datas → Arquivos).

**Requer:** Admin authentication

**Path Parameters:**
- `escola_id` (integer, required) - ID da escola

**Response (200 OK):**
```json
{
  "dashboard_completo": [
    {
      "divisao_logistica": "Norte",
      "dias_uteis": "15",
      "quantidade_total": 1250,
      "produtos": [
        {
          "id_produto": 106,
          "produto": "Apostila A4",
          "quantidade": 500,
          "datas": [
            {
              "data_saida": "2026-01-12",
              "quantidade": 500,
              "arquivos": [
                {
                  "arquivo": "apostila_matematica.pdf",
                  "copias": 250,
                  "paginas": 80
                },
                {
                  "arquivo": "apostila_portugues.pdf",
                  "copias": 250,
                  "paginas": 75
                }
              ]
            }
          ]
        },
        {
          "id_produto": 87,
          "produto": "Prova A4",
          "quantidade": 750,
          "datas": [
            {
              "data_saida": "2026-01-12",
              "quantidade": 750,
              "arquivos": [
                {
                  "arquivo": "prova_semestral.pdf",
                  "copias": 750,
                  "paginas": 4
                }
              ]
            }
          ]
        }
      ]
    },
    {
      "divisao_logistica": "Sul",
      "dias_uteis": "10",
      "quantidade_total": 800,
      "produtos": [
        {
          "id_produto": 106,
          "produto": "Apostila A4",
          "quantidade": 800,
          "datas": [
            {
              "data_saida": "2026-01-15",
              "quantidade": 800,
              "arquivos": [
                {
                  "arquivo": "apostila_ciencias.pdf",
                  "copias": 800,
                  "paginas": 60
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

**Errors:**
- `400 Bad Request` - ID da escola inválido
- `401 Unauthorized` - Token inválido
- `403 Forbidden` - Não é admin
- `500 Internal Server Error` - Erro no servidor

---

## Orçamento

### POST /api/orcamento/gerar

Gera orçamentos separados por unidade escolar baseado nos filtros fornecidos.

**Requer:** Admin authentication

**Request Body:**
```json
{
  "escola_id": 5,
  "ids_produtos": [106, 87],
  "datas_saida": ["2026-01-12", "2026-01-15"]
}
```

**Parameters:**
- `escola_id` (integer, required) - ID da escola
- `ids_produtos` (array of integers, required) - Lista de IDs dos produtos selecionados
- `datas_saida` (array of dates, required) - Lista de datas de saída no formato YYYY-MM-DD

**Response (200 OK):**
```json
{
  "orcamentos": [
    {
      "identifier": "PageFlow",
      "data": {
        "id_cliente": 123,
        "id_vendedor": 2285,
        "id_forma_pagamento": "11",
        "itens": [
          {
            "id_produto": 106,
            "descricao": "apostila_matematica (#45)",
            "quantidade": 250,
            "usar_listapreco": 1,
            "manter_estrutura_mod_produto": 1,
            "componentes": [
              {
                "id": 1,
                "descricao": "Capa",
                "altura": 29.7,
                "largura": 21.0,
                "quantidade_paginas": 1,
                "gramaturasubstratoimpressao": 180,
                "perguntas_componente": [
                  {
                    "id_pergunta": 10,
                    "pergunta": "Acabamento",
                    "tipo": "select",
                    "resposta": "Laminação fosca"
                  }
                ]
              },
              {
                "id": 2,
                "descricao": "Miolo",
                "altura": 29.7,
                "largura": 21.0,
                "quantidade_paginas": 80,
                "gramaturasubstratoimpressao": 75,
                "corfrente": "4x0",
                "corverso": "4x4",
                "perguntas_componente": []
              }
            ],
            "perguntas_gerais": [
              {
                "tipo": "select",
                "pergunta": "Tipo de grampo",
                "resposta": "Grampo grampeador",
                "id_pergunta": 25
              }
            ]
          }
        ]
      }
    },
    {
      "identifier": "PageFlow",
      "data": {
        "id_cliente": 124,
        "id_vendedor": 2285,
        "id_forma_pagamento": "11",
        "itens": [
          {
            "id_produto": 87,
            "descricao": "prova_semestral (#46)",
            "quantidade": 750,
            "usar_listapreco": 1,
            "manter_estrutura_mod_produto": 1,
            "componentes": [
              {
                "id": 3,
                "descricao": "Folha Prova",
                "altura": 29.7,
                "largura": 21.0,
                "quantidade_paginas": 4,
                "perguntas_componente": []
              }
            ],
            "perguntas_gerais": []
          }
        ]
      }
    }
  ],
  "total_unidades": 2
}
```

**Nota:** A resposta contém um orçamento para cada unidade escolar que possui pedidos correspondentes aos filtros.

**Errors:**
- `400 Bad Request` - Parâmetros inválidos (escola_id <= 0, listas vazias)
- `401 Unauthorized` - Token inválido
- `403 Forbidden` - Não é admin
- `500 Internal Server Error` - Erro no servidor

---

## Códigos de Status HTTP

- `200 OK` - Requisição bem-sucedida
- `400 Bad Request` - Parâmetros inválidos
- `401 Unauthorized` - Não autenticado ou token inválido
- `403 Forbidden` - Sem permissão (não é admin)
- `404 Not Found` - Recurso não encontrado
- `500 Internal Server Error` - Erro interno do servidor

---

## Notas de Segurança

1. **Apenas administradores** podem acessar todos os endpoints exceto `/api/auth/login`
2. Tokens JWT expiram após 8 horas (480 minutos)
3. Todas as queries SQL usam prepared statements (SQLAlchemy) para prevenir SQL Injection
4. Validação de entrada usando Pydantic schemas
5. CORS configurável para ambientes de produção
6. Logging de todas as requisições e erros

---

## Exemplos de Uso com cURL

### Login
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "senha123"}'
```

### Get Escolas
```bash
curl -X GET http://localhost:8000/api/dashboard/escolas \
  -H "Authorization: Bearer <seu-token-aqui>"
```

### Get Pedidos Cascata
```bash
curl -X GET http://localhost:8000/api/pedidos/escola/5/cascata \
  -H "Authorization: Bearer <seu-token-aqui>"
```

### Gerar Orçamento
```bash
curl -X POST http://localhost:8000/api/orcamento/gerar \
  -H "Authorization: Bearer <seu-token-aqui>" \
  -H "Content-Type: application/json" \
  -d '{
    "escola_id": 5,
    "ids_produtos": [106, 87],
    "datas_saida": ["2026-01-12"]
  }'
```

---

## Swagger UI

A documentação interativa da API está disponível em:
```
http://localhost:8000/docs
```

A documentação alternativa (ReDoc) está em:
```
http://localhost:8000/redoc
```
