# DESKFLOW2.0

Worker da **fila de processamento do PageFlow** (`fila_processamento`). O
PageFlow enfileira os itens; o DESKFLOW2.0 os executa contra o ERP Wingraph
(API da Bremen Sistemas) e grava o resultado na própria fila. Quem aplica o
resultado nas tabelas de domínio é o PageFlow (os "projetores").

O DESKFLOW2.0 só:

- faz o *claim* dos itens em `fila_processamento`;
- monta o corpo da chamada (no PCP, com os SQLs de `app/sql/`);
- chama o ERP (ou, no download, o Vercel Blob);
- grava o desfecho do item nas tabelas `fila_*`.

Nenhuma tabela de domínio é escrita daqui: a role `deskflow_fila` só tem GRANT
nas colunas de execução da fila.

## Sumário

- [Como funciona](#como-funciona)
- [Ciclos do worker](#ciclos-do-worker)
- [Regras de segurança](#regras-de-segurança)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Requisitos](#requisitos)
- [Instalação (Windows, on-prem)](#instalação-windows-on-prem)
- [Configuração (`.env`)](#configuração-env)
- [Comandos](#comandos)
- [Logs](#logs)
- [Testes](#testes)
- [Versionamento e releases](#versionamento-e-releases)

## Como funciona

```
PageFlow                         DESKFLOW2.0 (worker)                          destino
enfileira item ─▶ fila_processamento ──claim──▶ controller do tipo ──▶ ERP Wingraph / Vercel Blob
                                                 │
projetor lê o resultado ◀── resultado + estado ◀─┘
(webhook do ERP, no modo assíncrono, chega direto no PageFlow)
```

Cada `tipo_codigo` tem um controller em `app/controllers/`:

| Tipo | Chamada | Idempotente |
|---|---|---|
| `cliente.consultar` | `GET /api/v1/cliente` | sim |
| `cliente.criar` | `POST /api/v1/cliente` | não |
| `cliente.atualizar` | `PATCH /api/v1/cliente` | não |
| `cliente.planilha_linha` | delega a `cliente.criar` ou `cliente.atualizar` | não |
| `cliente.sincronizar_pagina` | `GET /api/v1/cliente?page=` | sim |
| `produto.importar` | `GET /api/v1/caracteristicasproduto` | sim |
| `pcp.orcamento.enviar` | `POST /api/v1/orcamento` | não |
| `pcp.aprovacao.enviar` | `GET /api/v1/proposta` + `POST /api/v1/proposta/aprovar` | não |
| `pcp.download_arquivos` | Vercel Blob → `DOWNLOAD_BASE_PATH` (só registrado com a pasta configurada) | sim |

Cada execução termina num destes desfechos: `concluido`, `falhou` (o destino
recusou; não gasta tentativa), `retentar` (certeza de que o destino não
processou), `incerto` (pode ter processado; tipos não idempotentes param para
verificação ou decisão humana) e `aguardando_callback` (aceito em modo
assíncrono; o resultado vem pelo webhook).

### Orçamentos do PCP

O SQL é escolhido só pela **origem** do lote (`orcamento_api_lotes.origem`):

| Origem | SQL | Orçamentos | Itens |
|---|---|---|---|
| escola | `sql/orcamento_agrupado.sql` (SQL Agrupado) | 1 por turma (pedidos sem turma formam um) | soma as unidades do mesmo item (`codigo_externo` = ids separados por vírgula) |
| **integração** | `sql/orcamento_integracao.sql` | 1 por pedido do parceiro (`integra_pedidos`) | 1 por produto (`codigo_externo` = id do `integra_pedido_produtos`) |

Até 2026-10-07 a origem escola tinha também o `sql/orcamento_unidade.sql` (1
orçamento por unidade, 1 item por pedido), escolhido pelo modo de agrupamento
marcado no "Enviar". O modo saiu do sistema — tela, payload e banco — e o
Agrupado ficou como o único. Orçamento antigo do modo unidade com pedidos de
mais de uma turma não é migrado: se reenviado, falha explicando que não pode
ser enviado como está (não há ação na tela que o redivida).

Quem divide o lote em orçamentos é o PageFlow, no clique "Enviar". O
DESKFLOW2.0 roda o SQL só com os ids daquele orçamento. Antes de mandar ao
ERP, confere se todo id virou item. Pedido sem arquivo, sem especificação ou
com produto fora do catálogo bloqueia o envio com uma mensagem clara na tela,
em vez de sumir do orçamento.

### Lotes de integração

Pedidos que chegam ao PageFlow pela API de integração (`integra_pedidos`)
usam as MESMAS tabelas `orcamento_api_*` e os mesmos tipos da fila — muda só de onde
vêm os itens. O consumidor não precisa saber mais que isso:

- `OrcamentoReivindicado.origem` diz qual lado vale, e `ids_origem` devolve a
  lista certa (`pedido_distribuicao_ids` ou `integra_pedido_produto_ids`);
- o cabeçalho (cliente/vendedor/forma) vem da requisição, como sempre — na
  integração o PageFlow o copiou do cadastro da integração, não da unidade;
- a estrutura do item (componentes, perguntas, tarefas) vem do **catálogo
  vivo** (`catalogo_bremen_modelos` e filhas), não de especificação de pedido.
  O snapshot do modelo existe no banco mas está vazio e não é lido: mudar o
  modelo no catálogo muda o que vai ao ERP nos envios seguintes;
- `altura`/`largura` saem como estão em
  `catalogo_bremen_modelo_componentes` (já em **centímetros**) — ao contrário
  do SQL de escola, que lê milímetros e divide por 10;
- capa e miolo são decididos por `bremen_componentes.is_capa/is_miolo`.

**Downloads de lote de integração**: os arquivos não estão em
`pedido_arquivos_pdf` — são URLs no próprio produto (`arquivo_pdf` e, quando
existirem, `design_capa_frente`/`design_capa_verso`; mockups e etiqueta ficam
de fora). A pasta de destino é
`<DOWNLOAD_BASE_PATH>/<integração> - <numero_pedido>/<op>/`, no lugar da
escola, e a linha de `downloads_bremen` (que o PageFlow grava a partir do
resultado) vai com `integra_pedido_produto_id` e `arquivo_pdf_id` NULL.

**Síncrono ou assíncrono** é decidido pelo PageFlow ao enfileirar: com
`url_webhook` no payload a chamada vai assíncrona e o item fica
`aguardando_callback`; sem ela, vai síncrona e a resposta do POST é o resultado.

## Ciclos do worker

Um único processo com APScheduler. Cada ciclo roda no máximo uma instância por
vez e ciclos atrasados não se acumulam. Um ciclo com erro não derruba o worker;
o próximo tenta de novo.

| Ciclo | Intervalo | O que faz |
|---|---|---|
| fila síncrona | `FILA_POLL_SINCRONO_SEGUNDOS` (1 s) | claim e execução no pool síncrono (1 por vez, em ordem) |
| fila assíncrona | `FILA_POLL_ASSINCRONO_SEGUNDOS` (5 s) | claim e execução no pool assíncrono (4 em paralelo) |
| reaper | `FILA_REAPER_SEGUNDOS` (30 s) | devolve itens com lease vencido |
| heartbeat | `FILA_HEARTBEAT_SEGUNDOS` (30 s) | registra o worker vivo em `fila_workers` |

Sem `LISTEN/NOTIFY`: o banco responde pelo pooler de transação do Supabase,
onde ele não funciona. O pickup é por poll curto.

## Regras de segurança

- **Sem duplicidade.** O ERP não é idempotente, então POST com timeout de
  leitura não é repetido: vira `incerto`. Só 503 e falha de conexão são
  repetidos, com backoff gravado em `disponivel_em`.
- **Aprovação nunca duas vezes.** Antes do POST de aprovação, a proposta é
  consultada; se todos os itens já estão confirmados, nenhum POST sai.
- **Transações curtas.** Nenhuma transação fica aberta durante uma chamada
  externa. O claim é commitado antes, e o lease é renovado enquanto a chamada
  está em voo.
- **Token fora da tela.** O `url_webhook` (que carrega o token) nunca é
  gravado em `payload_enviado`.
- **Download publicado inteiro.** O download é montado numa pasta temporária
  e só publicado no fim. Repetir o download baixa só o que falta.

## Estrutura do projeto

```
app/
├── __main__.py               # linha de comando (worker, verificar, dry-run)
├── core/                     # INFRAESTRUTURA — nada de regra de domínio
│   ├── config.py             # Settings (pydantic-settings, lê o .env)
│   ├── database.py           # engine SQLAlchemy do Postgres do PageFlow
│   ├── logging_config.py     # log em console e arquivo com rotação diária
│   ├── app.py                # monta as peças a partir da configuração
│   └── agendador.py          # os quatro ciclos da fila (APScheduler)
├── fila/                     # o MOTOR da fila, agnóstico de domínio
│   ├── motor.py              # executa um item: preparar -> chamar -> interpretar
│   ├── escalonador.py        # os dois pools (síncrono 1 / assíncrono 4)
│   ├── repositorio.py        # SQL da fila: claim SKIP LOCKED, lease, reaper, heartbeat
│   ├── catalogo.py           # status e tipos lidos do banco
│   ├── registry.py           # tipo_codigo -> handler
│   └── modelos.py            # Preparo, Desfecho, Estado, ItemReivindicado
├── controllers/              # ENTRADA: um handler por tipo_codigo, por módulo
│   ├── __init__.py           # registrar_erp_wingraph (registra todos no registry)
│   ├── payload_invalido.py   # PayloadInvalido: a recusa que os validators devolvem
│   ├── resposta_erp.py       # classifica a falha do ERP nos três baldes
│   ├── clientes/             # consultar, criar, atualizar, planilha, sincronizar_pagina
│   │   └── validators/
│   │       └── cliente_validator.py
│   ├── pcp/                  # orcamento_enviar, aprovacao_enviar, download_arquivos
│   │   ├── resposta_assincrona.py  # ack assíncrono -> aguardando_callback
│   │   └── validators/
│   │       └── pcp_validator.py
│   └── produtos/             # importar
│       └── validators/
│           └── produto_validator.py
├── servicos/                 # REGRAS DE NEGÓCIO, um subpacote por domínio
│   ├── clientes/
│   │   └── verificacao.py    # cliente existe? ERP já reflete a alteração?
│   └── pcp/
│       ├── payload.py        # monta o corpo do orçamento com os SQLs
│       ├── aprovacao.py      # monta o data da aprovação com o SQL
│       ├── envio.py          # modo assíncrono no corpo e corpo para auditoria
│       └── download_arquivos.py
├── repositorios/             # LEITURAS de domínio no banco
│   └── pcp.py                # orçamento e arquivos da aprovação
├── integracoes/
│   └── erp.py                # cliente do Wingraph: login, retry de 503, leitura do envelope
├── utils/
│   └── conversao.py          # so_digitos, inteiro_positivo, remover_nulos
└── sql/                      # SQLs que montam os corpos enviados ao ERP
tests/                        # unittest; tests/integracao/ fala com o Postgres
```

O caminho de um item da fila, camada por camada:

```
fila/motor  ──▶  controllers/<módulo>/<tipo>.py
                   ├─ controllers/<módulo>/validators/   o payload é aceitável?
                   ├─ servicos/<módulo>/...              regra de negócio (usa repositorios/ e sql/)
                   ├─ integracoes/erp.py                 chamada ao Wingraph
                   └─ controllers/resposta_erp.py        resposta -> Desfecho
```

Cada módulo tem o seu `validators/`, com as regras do PAYLOAD que entrou
(função pura, sem I/O); não há validador global.

Regras de dependência: `controllers` usam `servicos`, os próprios `validators`,
`integracoes` e `utils`; `servicos` nunca importam um controller; `utils` não
importa nada do projeto; `fila/` não conhece nenhum controller (o encontro dos
dois é só no `registry`).

A fronteira que separa `controllers/resposta_erp.py` dos `validators/` é a
**direção do dado**: validador olha o payload que ENTROU, antes de qualquer
chamada sair; `resposta_erp.py` olha a resposta que CHEGOU. Por isso
`PayloadInvalido` não é exceção — exceção no preparo é retentável por construção
no motor, e payload inválido não melhora na terceira tentativa.

## Requisitos

- Python 3.10 ou mais novo (desenvolvido com 3.13);
- acesso ao Postgres do PageFlow (com as tabelas `fila_*`);
- acesso à API do Wingraph;
- para o download: acesso de escrita à pasta de produção
  (`DOWNLOAD_BASE_PATH`) e o token do Vercel Blob.

## Instalação (Windows, on-prem)

O worker precisa rodar numa máquina que enxergue a pasta de produção
(`DOWNLOAD_BASE_PATH`).

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

Crie o `.env` na raiz do projeto (ver [Configuração](#configuração-env)) e
teste:

```bash
.venv\Scripts\python -m app verificar
```

O `verificar` testa o banco, mostra a configuração da fila, faz login no ERP e
mostra a pasta de download.

Serviço com NSSM:

```bash
nssm install Deskflow2 "C:\FLOW\DESKFLOW2.0\.venv\Scripts\python.exe" "-m app worker"
```

```bash
nssm set Deskflow2 AppDirectory "C:\FLOW\DESKFLOW2.0"
```

```bash
nssm start Deskflow2
```

## Configuração (`.env`)

A configuração vem do `.env` na pasta de onde o worker roda. Para usar outro
arquivo (ex.: `testing`), defina `DESKFLOW2_ENV_FILE=.env.testing`. Arquivos
`.env*` não são versionados. Chaves que sobrarem no `.env` (como as antigas
`PAGEFLOW_*` e `PCP_*`) são ignoradas.

### Obrigatórias

| Variável | Descrição |
|---|---|
| `DATABASE_URL` | o mesmo Postgres do PageFlow |
| `ERP_BASE_URL` | base da API do Wingraph |
| `ERP_USER` / `ERP_PASSWORD` | credenciais do ERP |

### Opcionais

| Variável | Padrão | Descrição |
|---|---|---|
| `DB_SSL` | `true` | `sslmode=require` na conexão |
| `DB_STATEMENT_TIMEOUT_MS` | `120000` | timeout de cada comando no banco |
| `DB_POOL_SIZE` | `0` | `0` dimensiona pelo que o processo abre |
| `DB_MAX_OVERFLOW` | `5` | conexões extras além do pool |
| `ERP_IDENTIFIER` | `PageFlow` | `identifier` enviado ao ERP |
| `ERP_TIMEOUT` | `120` | timeout das chamadas ao ERP (s) |
| `ERP_TOKEN_VIDA_SEGUNDOS` | `7200` | vida do token do ERP |
| `ERP_TOKEN_MARGEM_SEGUNDOS` | `300` | renova o token com esta antecedência |
| `ERP_503_MAX_WAIT_SECONDS` | `300` | janela para repetir 503 e falha de conexão |
| `ERP_503_RETRY_BASE_SECONDS` | `5` | espera inicial entre tentativas |
| `ERP_503_RETRY_MAX_INTERVAL_SECONDS` | `30` | espera máxima entre tentativas |
| `ERP_MAX_CONEXOES` | `6` | teto de conexões simultâneas ao ERP |
| `ERP_CONEXOES_RESERVADAS_SINCRONO` | `2` | conexões reservadas ao pool síncrono |
| `FILA_ATIVA` | `true` | `false` faz o `worker` sair sem iniciar |
| `FILA_DESTINO` | `erp_wingraph` | destino cujos itens este worker executa |
| `FILA_WORKER_ID` | `<host>:<pid>` | id do worker (único em `fila_workers`) |
| `FILA_POOL_SINCRONO` / `FILA_POOL_ASSINCRONO` | `1` / `4` | tamanho de cada pool |
| `FILA_POLL_SINCRONO_SEGUNDOS` / `FILA_POLL_ASSINCRONO_SEGUNDOS` | `1` / `5` | intervalo do poll |
| `FILA_LOTE_CLAIM` | `10` | teto de itens por claim |
| `FILA_LEASE_MARGEM_SEGUNDOS` | `30` | lease = timeout do tipo + margem |
| `FILA_REAPER_SEGUNDOS` / `FILA_HEARTBEAT_SEGUNDOS` | `30` / `30` | intervalo do reaper e do heartbeat |
| `FILA_BACKOFF_SINCRONO_BASE` / `FILA_BACKOFF_SINCRONO_TETO` | `2` / `8` | backoff da classe síncrona (s) |
| `FILA_BACKOFF_ASSINCRONO_BASE` / `FILA_BACKOFF_ASSINCRONO_TETO` | `15` / `900` | backoff da classe assíncrona (s) |
| `BLOB_READ_WRITE_TOKEN` | vazio | token do Vercel Blob, para baixar os arquivos |
| `DOWNLOAD_BASE_PATH` | vazio | pasta de produção; vazio desliga o download |
| `DOWNLOAD_TIMEOUT` | `120` | timeout de cada arquivo (s) |
| `DOWNLOAD_TENTATIVAS` | `3` | tentativas por arquivo |
| `LOG_DIR` | `logs` | pasta dos logs |
| `LOG_LEVEL` | `INFO` | nível do log |

Exemplo mínimo:

```dotenv
DATABASE_URL=postgresql://usuario:senha@host:5432/pageflow
ERP_BASE_URL=https://erp.exemplo.com.br
ERP_USER=usuario
ERP_PASSWORD=senha
DOWNLOAD_BASE_PATH=\\servidor\producao
BLOB_READ_WRITE_TOKEN=token
```

## Comandos

```bash
.venv\Scripts\python -m app worker
```

```bash
.venv\Scripts\python -m app verificar
```

```bash
.venv\Scripts\python -m app dry-run --orcamento 123
```

- `worker`: roda os ciclos da fila em loop (é o que o serviço executa).
- `verificar`: testa o banco e o login no ERP.
- `dry-run`: mostra o corpo que iria ao ERP para um orçamento, sem mudar nada.

## Logs

- `LOG_DIR/deskflow2.log`: log completo, com rotação à meia-noite (30 dias).
- `LOG_DIR/deskflow2_erros.log`: só os erros, mesma rotação.

## Testes

```bash
.venv\Scripts\python -m unittest discover -s tests -t .
```

Os testes de `tests/` não usam banco nem rede: o ERP e o Blob são simulados
com `httpx.MockTransport`. Os de `tests/integracao/` falam com o Postgres de
testing e são pulados quando ele não está disponível.

## Versionamento e releases

O desenvolvimento acontece na branch `developer`. As releases são geradas pelo
[release-please](https://github.com/googleapis/release-please) a cada push na
`main`, a partir de commits no padrão
[Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
`refactor:`, `perf:`...). A versão fica em `app/__init__.py` e em
`.release-please-manifest.json`.
