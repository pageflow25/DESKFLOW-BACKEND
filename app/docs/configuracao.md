
---

# 🧠 VISÃO GERAL DA SOLUÇÃO

Você tem:

* **Backend** (FastAPI / Python)

  * Roda **dentro da intranet**
  * Fica **sempre ligado no Windows Server**
  * Atualiza **automaticamente quando há commit na main**
* **Frontend** (Electron)

  * Instalado como **.exe no PC do usuário**
  * Continua recebendo **updates automáticos do GitHub**

Tudo isso sem precisar acessar manualmente o servidor.

---

# 🏗️ ARQUITETURA FINAL

```
DEV PC
  ↓ git push
GITHUB
  ↓
GitHub Actions
  ↓
Self-Hosted Runner (Windows Server)
  ↓
git pull + restart service
  ↓
Backend FastAPI (NSSM)
```

---

# PARTE 1 — BACKEND NO WINDOWS SERVER (PASSO A PASSO)

## 1️⃣ Estrutura correta no servidor

Exemplo real (o seu caso):

```
C:\Users\arthur.reis\Documents\
└── DESKFLOW\
    └── DESKFLOW-BACKEND\
        ├── app\
        ├── main.py
        ├── requirements.txt
        ├── .env
        └── venv\
```

> ⚠️ **IMPORTANTE**
> Serviços do Windows **funcionam melhor fora da pasta do usuário**.
> **Recomendado depois mover para**:

```
C:\deskflow\backend
```

Mas por enquanto vamos respeitar o seu caminho atual.

---

## 2️⃣ Criar o ambiente virtual CORRETAMENTE

No PowerShell:

```powershell
cd C:\Users\arthur.reis\Documents\DESKFLOW\DESKFLOW-BACKEND
python -m venv venv
```

Ativar:

```powershell
.\venv\Scripts\Activate.ps1
```

Instalar dependências:

```powershell
pip install -r requirements.txt
```

### ✅ Teste CRÍTICO

Execute:

```powershell
pip show uvicorn
```

Se **não aparecer**, o erro que você está tendo é explicado.

---

## 3️⃣ COMO RODAR UVICORN DO JEITO CERTO (IMPORTANTE)

No Windows, **nem sempre existe `uvicorn.exe`**.

👉 **FORMA CORRETA E UNIVERSAL**:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

✔️ Se isso **funcionar**, o backend está OK
✔️ Se isso **não funcionar**, o problema **não é o NSSM**, é o código/env

---

# PARTE 2 — CRIAR O SERVIÇO NO WINDOWS COM NSSM

## 4️⃣ Instalar o NSSM

* Baixe em: **[https://nssm.cc](https://nssm.cc)**
* Copie `nssm.exe` para:

```
C:\Windows\System32
```

Teste:

```powershell
nssm --version
```

---

## 5️⃣ Criar o serviço (FORMA CORRETA)

```powershell
nssm install DeskflowBackend
```

### Configure assim 👇

### 🔹 Aba **Application**

**Path**

```
C:\Users\arthur.reis\Documents\DESKFLOW\DESKFLOW-BACKEND\venv\Scripts\python.exe
```

**Startup directory**

```
C:\Users\arthur.reis\Documents\DESKFLOW\DESKFLOW-BACKEND
```

**Arguments**

```
-m uvicorn main:app --host 0.0.0.0 --port 8000
```

> 🔥 Esse é o erro que estava te travando:
> **NÃO usar `uvicorn.exe`**, usar `python -m uvicorn`.

---

### 🔹 Aba **Details**

* Display name: `Deskflow Backend API`
* Startup type: `Automatic`

---

### 🔹 Aba **I/O (OBRIGATÓRIA para debug)**

```
Stdout: C:\deskflow\backend\service.log
Stderr: C:\deskflow\backend\error.log
```

Clique **Install service**

---

## 6️⃣ Iniciar e testar

```powershell
Start-Service DeskflowBackend
```

Ver status:

```powershell
Get-Service DeskflowBackend
```

Teste no navegador:

```
http://IP_DO_SERVIDOR:8000/docs
```

---

## 7️⃣ Se der erro (SEM DESESPERO)

Abra:

```
C:\deskflow\backend\error.log
```

Ali estará **o erro real** (variável de ambiente, import, banco etc.)

---

# PARTE 3 — ATUALIZAÇÃO AUTOMÁTICA (CI/CD)

## 8️⃣ O PROBLEMA

O GitHub **não entra na intranet**, então:
❌ Webhook tradicional não funciona

✅ **Solução profissional**: **Self-Hosted Runner**

---

## 9️⃣ Instalar GitHub Runner no Windows Server

No GitHub:

```
Repo → Settings → Actions → Runners → New self-hosted runner
```

Escolha **Windows**

No servidor:

```powershell
mkdir C:\actions-runner
cd C:\actions-runner
```

Execute os comandos que o GitHub fornecer.

### ⚠️ Quando perguntar:

```
Run as service? → Y
```

Isso é **OBRIGATÓRIO**

---

## 🔟 Workflow de Deploy Automático

Crie no repositório:

```
.github/workflows/deploy-backend.yml
```

```yaml
name: Deploy Backend Windows

on:
  push:
    branches: [ "main" ]
    paths:
      - "backend/**"

jobs:
  deploy:
    runs-on: self-hosted

    steps:
      - uses: actions/checkout@v4

      - name: Atualizar Backend
        shell: powershell
        run: |
          cd C:\deskflow
          git pull origin main
          cd backend
          .\venv\Scripts\python.exe -m pip install -r requirements.txt
          Restart-Service DeskflowBackend
```

---

## 🔁 O QUE ACONTECE AGORA?

1. Você dá `git push`
2. GitHub avisa o Runner
3. Runner executa:

   * `git pull`
   * `pip install`
   * `Restart-Service`
4. Backend sobe atualizado **sozinho**

⏱️ Tempo médio: **20–40 segundos**

---

# PARTE 4 — FRONTEND (RESUMO)

* Electron usa **electron-updater**
* Builds são feitos pelo **GitHub Actions**
* Usuário recebe update automático via **GitHub Releases**
* Backend e frontend evoluem **independentes**

---

# ✅ CHECKLIST FINAL

✔ Backend rodando como serviço
✔ Reinicia sozinho
✔ Atualiza automaticamente
✔ Logs configurados
✔ CI/CD funcionando
✔ Arquitetura profissional

---

## 📌 Próximo passo recomendado

👉 **Mover o backend para `C:\deskflow\backend`**
👉 **Configurar HTTPS interno (opcional)**
👉 **Adicionar healthcheck (`/health`)**

Se quiser, no próximo passo eu posso:

* Ajustar seu `.env`
* Validar seu `main.py`
* Criar rollback automático
* Criar script de backup antes do deploy

Só me dizer.
