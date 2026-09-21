# DESKFLOW2.0

Consumidor da fila do **PCP do PageFlow**. Pega os orçamentos que o usuário
enviou pela tela do PCP, gera o orçamento no ERP Wingraph, aprova (quando o
lote pediu) e baixa os arquivos da OP para a pasta de produção.

O PageFlow é o **único que grava o resultado**. O DESKFLOW2.0 só:

- lê as pendências no banco e faz o *claim*;
- monta o corpo do orçamento com os SQLs de `deskflow2/sql/`;
- chama o ERP;
- devolve o resultado ao PageFlow por endpoint.

O contrato completo está em
`PAGEFLOW/BACKEND_PAGEFLOW/docs/pcp/modulo-pcp.md` (seções 12 a 15).

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
ERP, confere se todo pedido virou item: pedido sem arquivo, sem especificação
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

## Ciclos do worker

| Ciclo | Intervalo | O que faz |
|---|---|---|
| orçamentos | `PCP_ENVIO_INTERVALO_SEGUNDOS` | claim → SQL → POST `/api/v1/orcamento` |
| aprovações | idem | GET `/api/v1/proposta` (não aprova duas vezes) → POST `/api/v1/proposta/aprovar` |
| downloads | idem | baixa os arquivos das OPs das aprovações de lotes com "baixar arquivos" |
| reconciliação | `PCP_RECONCILIACAO_MINUTOS`, entre 1 e 5 min (1ª rodada 15s após subir) | reenvia ao PageFlow o que ele não recebeu; destrava linhas paradas em `aguardando_retorno` via GET `/api/v1/requisicao` |

**Regras de segurança:**

- **Sem duplicidade.** O ERP não é idempotente, então POST com timeout de
  leitura não é repetido. Vira erro na tela com o aviso "confira no ERP antes
  de reenviar". Só 503 e falha de conexão são repetidos, dentro da janela
  `ERP_503_MAX_WAIT_SECONDS`.
- **Nada é reivindicado com o ERP fora.** Se o login no ERP falha, o ciclo
  não reivindica nada.
- **Nenhum resultado se perde.** Se o PageFlow estiver fora, o resultado fica
  em `DADOS_DIR/repasses_pendentes/` e é reenviado pela reconciliação.
- **Token fora da tela.** O `url_webhook` (que carrega o token) nunca é
  gravado em `payload_enviado`.
- **Download publicado inteiro.** O download é montado numa pasta temporária
  e só publicado no fim. Repetir o download baixa só o que falta.

## Instalação (Windows, on-prem)

O worker precisa rodar numa máquina que enxergue a pasta de produção
(`DOWNLOAD_BASE_PATH`).

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

Copie `.env.example` para `.env` e preencha:

- `DATABASE_URL`: o mesmo Postgres do PageFlow;
- `ERP_*`;
- `PAGEFLOW_API_URL`: a base do backend, **sem** `/api`;
- `PAGEFLOW_API_KEY`: chave criada no PageFlow em **Integrações**, com a
  permissão `pcp_retorno`;
- `DOWNLOAD_BASE_PATH`.

Teste a configuração:

```bash
.venv\Scripts\python -m deskflow2 verificar
```

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
  ambiente**. É a base do webhook que o ERP chama no modo assíncrono.

## Comandos

```bash
.venv\Scripts\python -m deskflow2 worker
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
- `dry-run`: mostra o corpo que iria ao ERP, sem mudar nada.
- `despachar`: despacha um orçamento pendente agora.
- `ciclo`: roda um ciclo uma vez (`orcamentos`, `aprovacoes`, `downloads` ou
  `reconciliacao`).

Para usar outro arquivo de configuração (ex.: `testing`), defina
`DESKFLOW2_ENV_FILE=.env.testing`.

## Testes

```bash
.venv\Scripts\python -m unittest discover -s tests -t .
```

Os testes rodam sem banco e sem rede. O ERP, o PageFlow e o Blob são
simulados com `httpx.MockTransport`.
