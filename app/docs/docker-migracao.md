# Migração do DESKFLOW Backend para Docker

## Diagnóstico da aplicação atual

- Backend Python/FastAPI iniciado por `main.py` na porta `8000`.
- Processo atual em produção: serviço Windows via NSSM executando `python -m uvicorn main:app --host 0.0.0.0 --port 8000`.
- Configuração por `.env` usando `pydantic-settings`.
- Banco PostgreSQL externo via `DATABASE_URL`, com SSL controlado por `DB_SSL`.
- Health check já disponível em `/healthz`.
- Logs são enviados para stdout e também para arquivos em `./logs`.
- Downloads de OPs usam `DOWNLOAD_BASE_PATH`.
- Arquivos temporários de orçamento usam `./temp_orcamentos`.
- Há scheduler interno com APScheduler para automação de conveniados.

## Desafios de conteinerização

1. Persistência de arquivos: `logs`, `temp_orcamentos` e downloads Bremen não podem ficar apenas dentro do container.
2. Caminhos Windows: `DOWNLOAD_BASE_PATH=C:/Bremen/OPs` deve virar um caminho Linux dentro do container, por exemplo `/data/bremen-ops`, montado em uma pasta do host.
3. Scheduler interno: se mais de um container rodar ao mesmo tempo com `CONVENIADO_AUTOMACAO_ATIVA=true`, os jobs agendados podem executar duplicados.
4. Banco externo: o container precisa resolver o host do PostgreSQL e acessar a rede/porta do banco.
5. Segredos: `.env` não deve ir para a imagem nem para o repositório.
6. Deploy atual: o workflow ainda reinicia o serviço NSSM. A troca para Docker deve ser feita em etapa separada, depois de teste paralelo.

## Arquivos criados

- `Dockerfile`: imagem Python slim com Uvicorn e healthcheck.
- `.dockerignore`: evita copiar `.env`, ambiente virtual, logs e dados para a imagem.
- `docker-compose.yml`: serviço principal com porta, volumes, `.env`, restart automático e healthcheck.
- `.env.docker.example`: referência das variáveis sem segredos.

## Persistência

No `docker-compose.yml`:

- `./logs:/app/logs`: preserva arquivos de log e permite auditoria no host.
- `./temp_orcamentos:/app/temp_orcamentos`: preserva JSONs temporários usados pelas rotas de orçamento.
- `./data/bremen-ops:/data/bremen-ops`: preserva PDFs baixados da Bremen/Vercel Blob.

Em produção, você pode trocar os bind mounts por caminhos fixos do servidor:

```yaml
volumes:
  - C:/deskflow/backend/logs:/app/logs
  - C:/deskflow/backend/temp_orcamentos:/app/temp_orcamentos
  - C:/Bremen/OPs:/data/bremen-ops
```

Mantenha `DOWNLOAD_BASE_PATH=/data/bremen-ops` dentro do container.

## Logs

A aplicação já grava em stdout e em arquivos. No Docker você ganha dois caminhos:

- `docker compose logs -f deskflow-backend` para logs do container.
- Pasta montada `./logs` para os arquivos rotativos já existentes.

Em produção, monitore também o tamanho da pasta `logs`, porque a rotação atual acontece por arquivo diário com backups.

## Reinicialização automática

O Compose usa:

```yaml
restart: unless-stopped
```

Isso substitui a função principal do NSSM: manter o processo ativo e reiniciar após falha/reboot do Docker Engine.

Para funcionar depois de reiniciar a máquina, duas coisas precisam estar ativas:

1. O serviço do Docker precisa iniciar junto com o Windows.
2. O container precisa ter política de restart, já configurada como `unless-stopped`.

Em Windows, valide o serviço do Docker:

```powershell
Get-Service docker
Set-Service docker -StartupType Automatic
Start-Service docker
```

Depois que o container for criado uma vez com `docker compose up -d`, o Docker guarda a política de restart. Se o Windows reiniciar, o serviço `docker` sobe e o container `deskflow-backend` volta automaticamente.

Se estiver usando Docker Desktop em vez de Docker Engine/serviço Docker no servidor, garanta que ele também inicialize com o Windows. Para produção em servidor, prefira uma instalação em que o Docker rode como serviço do sistema, sem depender de login interativo de usuário.

## Comandos básicos

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f deskflow-backend
Invoke-WebRequest http://localhost:8000/healthz
```

Validar reinício automático:

```powershell
Restart-Service docker
Start-Sleep -Seconds 20
docker compose ps
Invoke-WebRequest http://localhost:8000/healthz
```

Parar:

```powershell
docker compose down
```

Atualizar imagem após novo código:

```powershell
docker compose build
docker compose up -d
```

## Vantagens de Docker sobre NSSM

- Ambiente mais reprodutível: Python e dependências ficam fixos na imagem.
- Deploy e rollback mais previsíveis: uma versão de imagem pode ser promovida ou revertida.
- Healthcheck nativo e estado visível por `docker compose ps`.
- Menos dependência de ambiente virtual manual no Windows.
- Facilita homologação com a mesma imagem usada em produção.
- Melhor isolamento de arquivos, variáveis e runtime.

## Desvantagens e riscos

- Docker adiciona uma camada operacional nova no servidor.
- Volumes precisam ser planejados para não perder PDFs, temporários e logs.
- Caminhos gravados no banco em `downloads_bremen.caminho_local` passarão a ser caminhos internos do container se nada for ajustado.
- Jobs do scheduler podem duplicar se houver múltiplas réplicas ativas.
- Troubleshooting muda: passa por `docker logs`, healthcheck e inspeção de containers.
- Em Windows Server, é preciso garantir Docker Engine/WSL2 ou Docker compatível com o ambiente.

## Impactos esperados

- Desempenho: para esta API, o overhead tende a ser pequeno. O maior cuidado é I/O em volumes e latência até o PostgreSQL externo.
- Manutenção: melhora a previsibilidade de dependências, mas exige disciplina com imagens, tags e volumes.
- Monitoramento: fica mais fácil observar status/health do processo, mas convém adicionar monitoramento externo do `/healthz`.
- Atualizações: tendem a ficar mais seguras quando feitas como build de imagem + troca controlada, em vez de alterar venv em produção.

## Boas práticas por ambiente

Desenvolvimento:

- Usar `.env` local sem segredos de produção.
- Rodar `docker compose up --build`.
- Manter volumes locais para logs e temporários.

Homologação:

- Usar banco e credenciais separados da produção.
- Testar geração, aprovação, download e scheduler com `CONVENIADO_AUTOMACAO_ATIVA=false` inicialmente.
- Validar permissões do volume de downloads.

Produção:

- Usar `.env` fora do repositório e com backup seguro.
- Montar `logs`, `temp_orcamentos` e OPs em pastas fixas do servidor.
- Rodar apenas uma instância com scheduler ativo.
- Monitorar `/healthz`, uso de disco dos volumes e falhas de conexão com PostgreSQL/Bremen.
- Fazer rollback mantendo o NSSM disponível até a nova operação estabilizar.

## Migração com menor risco

1. Preparar Docker no servidor sem parar o NSSM.
2. Copiar o `.env` atual para o mesmo diretório do compose.
3. Ajustar no compose `DOWNLOAD_BASE_PATH=/data/bremen-ops` e mapear o volume para a pasta real desejada.
4. Subir o container em porta alternativa para teste, por exemplo `8001:8000`, enquanto o NSSM segue em `8000`.
5. Testar `http://IP_DO_SERVIDOR:8001/healthz` e `http://IP_DO_SERVIDOR:8001/docs`.
6. Validar login, consultas, geração de orçamento, aprovação e download em homologação ou com caso controlado.
7. Manter `CONVENIADO_AUTOMACAO_ATIVA=false` no container durante testes paralelos para evitar duplicidade.
8. Fazer uma janela curta de troca: parar NSSM, alterar Compose para `8000:8000`, subir container.
9. Validar `/healthz`, logs e fluxo crítico.
10. Manter NSSM instalado por alguns dias como rollback rápido, mas parado.
11. Depois da estabilização, atualizar o workflow de deploy para `docker compose build && docker compose up -d`.

## Remoção definitiva do NSSM

Remova o NSSM só depois de confirmar que:

- `Get-Service docker` está com `StartupType` automático.
- `docker compose ps` mostra o container como `running` ou `healthy`.
- A API responde em `/healthz` após reiniciar o serviço Docker.
- A API responde em `/healthz` após reiniciar a máquina.
- Logs e downloads estão sendo gravados nos diretórios montados do host.

Quando isso estiver validado, o serviço antigo pode ser removido:

```powershell
Stop-Service DeskflowBackend
nssm remove DeskflowBackend confirm
```

Depois disso, a cadeia de inicialização fica:

```text
Windows inicia
  -> Serviço Docker inicia automaticamente
  -> Docker lê a política restart: unless-stopped
  -> Container deskflow-backend sobe
  -> Uvicorn inicia a API dentro do container
  -> Healthcheck valida /healthz
```

Rollback rápido:

```powershell
docker compose down
Start-Service DeskflowBackend
```
