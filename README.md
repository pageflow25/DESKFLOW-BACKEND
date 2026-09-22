# DESKFLOW2.0

Consumidor da fila do **PCP do PageFlow**. Pega os orçamentos que o usuário
enviou pela tela do PCP, gera o orçamento no ERP Wingraph (API da Bremen
Sistemas), aprova (quando o lote pediu) e baixa os arquivos da OP para a pasta
de produção.

O PageFlow é o **único que grava o resultado**. O DESKFLOW2.0 só:

- lê as pendências no banco e faz o *claim*;
- monta o corpo do orçamento com os SQLs de `deskflow2/sql/`;
- chama o ERP;
- devolve o resultado ao PageFlow por endpoint.

O contrato completo está em
`PAGEFLOW/BACKEND_PAGEFLOW/docs/pcp/modulo-pcp.md` (seções 12 a 15).

## Sumário

- [Como funciona](#como-funciona)
- [Ciclos do worker](#ciclos-do-worker)
- [Regras de segurança](#regras-de-segurança)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Requisitos](#requisitos)
- [Instalação (Windows, on-prem)](#instalação-windows-on-prem)
- [Configuração (`.env`)](#configuração-env)
- [Comandos](#comandos)
- [Logs e dados locais](#logs-e-dados-locais)
- [Testes](#testes)
- [Versionamento e releases](#versionamento-e-releases)

## Como funciona

```
PageFlow (clique "Enviar")          DESKFLOW2.0 (worker)                        Wingraph
lote: modo + par + decisões
 └ requisição (cliente/vendedor/forma)
    └ orçamento pendente_envio ──claim──▶ SQL do modo ──▶ POST /api/v1/orcamento ──▶
                                          assíncrono: grava id_requisicao ─── webhook ──▶ PageFlow
                                          síncrono: repassa a resposta ──────────────────▶ PageFlow
    └ aprovação pendente_envio ──▶ GET /proposta (consulta) ──▶ POST /proposta/aprovar ──▶ idem
    └ aprovação com OP + "baixar arquivos" ──▶ Vercel Blob ──▶ <DOWNLOAD_BASE_PATH>/<escola>/<op>/
                                                             └─▶ PageFlow /retorno/aprovacoes/:id/downloads
```

| Modo do lote (`modo_agrupamento`) | SQL | Orçamentos | Itens |
|---|---|---|---|
| `unidade` (normal) | `sql/orcamento_unidade.sql` | 1 por unidade escolar | 1 por pedido (`codigo_externo` = id do pedido) |
| `escola` | `sql/orcamento_escola.sql` | 1 por turma (pedidos sem turma formam um) | soma as unidades do mesmo item (`codigo_externo` = ids separados por vírgula) |

Quem divide o lote em orçamentos é o PageFlow, no clique "Enviar". O
DESKFLOW2.0 roda o SQL só com os pedidos daquele orçamento. Antes de mandar ao
ERP, confere se todo pedido virou item. Pedido sem arquivo, sem especificação
ou com produto fora do catálogo bloqueia o envio com uma mensagem clara na
tela, em vez de sumir do orçamento.

Os SQLs originais estão em `docs/legado/`, para referência. Os novos foram
comparados com eles no banco `testing` (148 de 148 itens idênticos) e só
mudam no que o PCP exige:

- entram os ids exatos dos pedidos, e não mais filtros;
- o cabeçalho vem do par cliente/vendedor escolhido na tela;
- cada item ganha `codigo_externo`;
- o papel é lido em três eixos;
- `tarefas_gerais` passa a sair com `id`.

### Rotas usadas

| Sistema | Rota | Uso |
|---|---|---|
| Wingraph | `POST /api/v1/auth` | login (token de ~2h, sem refresh: renovado antes de vencer) |
| Wingraph | `POST /api/v1/orcamento` | gera o orçamento |
| Wingraph | `GET /api/v1/proposta` | consulta a proposta antes de aprovar |
| Wingraph | `POST /api/v1/proposta/aprovar` | aprova a proposta |
| Wingraph | `GET /api/v1/requisicao?id=` | consulta requisição assíncrona (reconciliação) |
| PageFlow | `POST /api/pcp/retorno/orcamentos/:id` | resultado do orçamento |
| PageFlow | `POST /api/pcp/retorno/aprovacoes/:id` | resultado da aprovação |
| PageFlow | `POST /api/pcp/retorno/aprovacoes/:id/consulta` | consulta prévia da proposta |
| PageFlow | `POST /api/pcp/retorno/aprovacoes/:id/downloads` | desfecho do download |

## Ciclos do worker

Um único processo com APScheduler. Cada ciclo roda no máximo uma instância por
vez e ciclos atrasados não se acumulam. Ciclos diferentes podem rodar ao mesmo
tempo: o claim CAS no banco garante que nenhuma linha é despachada duas vezes.
Um ciclo com erro não derruba o worker; o próximo tenta de novo.

| Ciclo | Intervalo | O que faz |
|---|---|---|
| orçamentos | `PCP_ENVIO_INTERVALO_SEGUNDOS` (mín. 10s) | claim → SQL → POST `/api/v1/orcamento` |
| aprovações | idem | GET `/api/v1/proposta` (não aprova duas vezes) → POST `/api/v1/proposta/aprovar` |
| downloads | idem | baixa os arquivos das OPs das aprovações de lotes com "baixar arquivos" |
| reconciliação | `PCP_RECONCILIACAO_MINUTOS`, entre 1 e 5 min (1ª rodada 15s após subir) | reenvia ao PageFlow o que ele não recebeu; destrava linhas paradas em `aguardando_retorno` via GET `/api/v1/requisicao` |

## Regras de segurança

- **Sem duplicidade.** O ERP não é idempotente, então POST com timeout de
  leitura não é repetido. Vira erro na tela com o aviso "confira no ERP antes
  de reenviar". Só 503 e falha de conexão são repetidos, dentro da janela
  `ERP_503_MAX_WAIT_SECONDS`.
- **Nada é reivindicado com o ERP fora.** Se o login no ERP falha, o ciclo
  não reivindica nada.
- **Nenhum resultado se perde.** Se o PageFlow estiver fora, o resultado fica
  em `DADOS_DIR/repasses_pendentes/` e é reenviado pela reconciliação. O
  retorno do PageFlow é idempotente (repetir devolve `ja_processado`).
- **Nada novo com repasse pendente.** Enquanto houver resultado guardado em
  `repasses_pendentes/`, os despachos de orçamento e aprovação não reivindicam
  nada — cada linha nova ficaria presa em `aguardando_retorno`. Voltam sozinhos
  quando a reconciliação entrega (ou o PageFlow descarta com 409) o que estava
  guardado.
- **Transações curtas.** Nenhuma transação fica aberta durante uma chamada
  HTTP. O claim é gravado e commitado antes de chamar o ERP.
- **Token fora da tela.** O `url_webhook` (que carrega o token) nunca é
  gravado em `payload_enviado`.
- **Download publicado inteiro.** O download é montado numa pasta temporária
  e só publicado no fim. Repetir o download baixa só o que falta.

## Estrutura do projeto

```
deskflow2/
├── __main__.py            # linha de comando (worker, verificar, dry-run, ...)
├── app.py                 # monta as peças a partir da configuração
├── config.py              # Settings (pydantic-settings, lê o .env)
├── db.py                  # engine SQLAlchemy do Postgres do PageFlow
├── jobs.py                # agendamento dos ciclos (APScheduler)
├── logging_config.py      # log em console e arquivo com rotação diária
├── clientes/
│   ├── erp.py             # cliente do Wingraph: login, retry de 503
│   └── pageflow.py        # repasse dos resultados ao PageFlow
├── repositorios/
│   ├── fila.py            # leitura e claim das filas orcamento_api_*
│   └── status.py          # catálogo de status do PCP
├── servicos/
│   ├── despacho_orcamento.py
│   ├── despacho_aprovacao.py
│   ├── download_arquivos.py
│   ├── reconciliacao.py
│   ├── repasse.py         # guarda e reenvia repasses não entregues
│   ├── payload.py         # monta o corpo do orçamento com os SQLs
│   └── comum.py
└── sql/
    ├── orcamento_unidade.sql
    └── orcamento_escola.sql
docs/legado/               # SQLs do DESKFLOW antigo, só para referência
tests/                     # unittest, sem banco e sem rede
```

## Requisitos

- Python 3.10 ou mais novo (desenvolvido com 3.13);
- acesso ao Postgres do PageFlow;
- acesso à API do Wingraph e ao backend do PageFlow;
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
.venv\Scripts\python -m deskflow2 verificar
```

O `verificar` testa o banco (e se o catálogo de status do PCP existe), faz
login no ERP e mostra o modo de envio e a pasta de download.

Serviço com NSSM (mesmo esquema do `DeskflowBackend`):

```bash
nssm install Deskflow2 "C:\FLOW\DESKFLOW2.0\.venv\Scripts\python.exe" "-m deskflow2 worker"
```

```bash
nssm set Deskflow2 AppDirectory "C:\FLOW\DESKFLOW2.0"
```

```bash
nssm start Deskflow2
```

**No PageFlow, antes de ligar:**

- as migrations `20260918000000` e `20260918000001` precisam estar aplicadas;
- `BACKEND_PUBLIC_BASE_URL` precisa ser a URL pública do backend **daquele
  ambiente**. É a base do webhook que o ERP chama no modo assíncrono;
- a chave de API do DESKFLOW2.0 precisa existir em **Integrações**, com a
  permissão `pcp_retorno`.

## Configuração (`.env`)

A configuração vem do `.env` na pasta de onde o worker roda. Para usar outro
arquivo (ex.: `testing`), defina `DESKFLOW2_ENV_FILE=.env.testing`. Arquivos
`.env*` não são versionados.

### Obrigatórias

| Variável | Descrição |
|---|---|
| `DATABASE_URL` | o mesmo Postgres do PageFlow (as tabelas `orcamento_api_*` são dele) |
| `ERP_BASE_URL` | base da API do Wingraph |
| `ERP_USER` / `ERP_PASSWORD` | credenciais do ERP |
| `PAGEFLOW_API_URL` | base do backend do PageFlow, **sem** `/api` (se vier com `/api`, é removido) |
| `PAGEFLOW_API_KEY` | chave criada no PageFlow em **Integrações**, com a permissão `pcp_retorno` |

### Opcionais

| Variável | Padrão | Descrição |
|---|---|---|
| `DB_SSL` | `true` | `sslmode=require` na conexão |
| `DB_STATEMENT_TIMEOUT_MS` | `120000` | timeout de cada comando no banco |
| `ERP_IDENTIFIER` | `PageFlow` | `identifier` enviado ao ERP (login, orçamento e aprovação) |
| `ERP_TIMEOUT` | `120` | timeout das chamadas ao ERP (s) |
| `ERP_TOKEN_VIDA_SEGUNDOS` | `7200` | vida do token do ERP |
| `ERP_TOKEN_MARGEM_SEGUNDOS` | `300` | renova o token com esta antecedência |
| `ERP_503_MAX_WAIT_SECONDS` | `300` | janela para repetir 503 e falha de conexão |
| `ERP_503_RETRY_BASE_SECONDS` | `5` | espera inicial entre tentativas |
| `ERP_503_RETRY_MAX_INTERVAL_SECONDS` | `30` | espera máxima entre tentativas |
| `PAGEFLOW_TIMEOUT` | `30` | timeout das chamadas ao PageFlow (s) |
| `PAGEFLOW_TENTATIVAS` | `5` | tentativas de repasse antes de guardar localmente |
| `PCP_MODO_ENVIO` | `assincrono` | `assincrono` (resultado pelo webhook) ou `sincrono` |
| `PCP_ENVIO_ATIVO` | `true` | `false` faz o `worker` sair sem iniciar |
| `PCP_ENVIO_INTERVALO_SEGUNDOS` | `60` | intervalo dos ciclos de orçamentos, aprovações e downloads |
| `PCP_ENVIO_LOTE_MAXIMO` | `20` | linhas processadas por ciclo |
| `PCP_PAUSA_ENTRE_ENVIOS_SEGUNDOS` | `3` | pausa entre chamadas ao ERP no mesmo ciclo |
| `PCP_RECONCILIACAO_MINUTOS` | `30` | idade mínima para reconciliar; também define o intervalo do ciclo (entre 1 e 5 min) |
| `PCP_RECONCILIACAO_LIMITE_HORAS` | `6` | depois disso, desiste da linha (o ERP tenta o webhook 5x, de hora em hora) |
| `PCP_DOWNLOAD_REINICIO_MINUTOS` | `60` | tempo para retomar um download que ficou parado |
| `BLOB_READ_WRITE_TOKEN` | vazio | token do Vercel Blob, para baixar os arquivos |
| `DOWNLOAD_BASE_PATH` | vazio | pasta de produção; vazio desliga o download |
| `DOWNLOAD_TIMEOUT` | `120` | timeout de cada arquivo (s) |
| `DOWNLOAD_TENTATIVAS` | `3` | tentativas por arquivo |
| `DADOS_DIR` | `dados` | onde ficam os repasses não entregues |
| `LOG_DIR` | `logs` | pasta dos logs |
| `LOG_LEVEL` | `INFO` | nível do log |

Exemplo mínimo:

```dotenv
DATABASE_URL=postgresql://usuario:senha@host:5432/pageflow
ERP_BASE_URL=https://erp.exemplo.com.br
ERP_USER=usuario
ERP_PASSWORD=senha
PAGEFLOW_API_URL=https://api.pageflow.exemplo.com.br
PAGEFLOW_API_KEY=chave
DOWNLOAD_BASE_PATH=\\servidor\producao
BLOB_READ_WRITE_TOKEN=token
```

## Comandos

```bash
.venv\Scripts\python -m deskflow2 worker
```

```bash
.venv\Scripts\python -m deskflow2 verificar
```

```bash
.venv\Scripts\python -m deskflow2 dry-run --orcamento 123
```

```bash
.venv\Scripts\python -m deskflow2 despachar --orcamento 123
```

```bash
.venv\Scripts\python -m deskflow2 ciclo reconciliacao
```

- `worker`: roda todos os ciclos em loop (é o que o serviço executa).
- `verificar`: testa o banco e o login no ERP.
- `dry-run`: mostra o corpo que iria ao ERP, sem mudar nada.
- `despachar`: despacha um orçamento pendente agora.
- `ciclo`: roda um ciclo uma vez (`orcamentos`, `aprovacoes`, `downloads` ou
  `reconciliacao`).

## Logs e dados locais

- `LOG_DIR/deskflow2.log`: log completo, com rotação à meia-noite (30 dias).
- `LOG_DIR/deskflow2_erros.log`: só os erros, mesma rotação.
- `DADOS_DIR/repasses_pendentes/`: resultados que o PageFlow ainda não
  recebeu. **Não apague**: a reconciliação reenvia e remove sozinha.

## Testes

```bash
.venv\Scripts\python -m unittest discover -s tests -t .
```

Os testes rodam sem banco e sem rede. O ERP, o PageFlow e o Blob são
simulados com `httpx.MockTransport`.

## Versionamento e releases

O desenvolvimento acontece na branch `developer`. As releases são geradas pelo
[release-please](https://github.com/googleapis/release-please) a cada push na
`main`, a partir de commits no padrão
[Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
`refactor:`, `perf:`...). A versão fica em `deskflow2/__init__.py` e em
`.release-please-manifest.json`.
