# Deploy no EasyPanel (Hostinger VPS)

## Pré-requisitos
- VPS com EasyPanel instalado
- Repositório Git (GitHub, GitLab, etc.)

---

## Passo 1: Subir código para Git

```bash
cd /Users/eryk/Documents/Dashboards/faturamento-dashboard

# Inicializar git (se ainda não tiver)
git init

# Adicionar arquivos
git add .

# Commit
git commit -m "Initial commit - Faturamento Dashboard"

# Criar repositório no GitHub e adicionar remote
git remote add origin https://github.com/SEU_USUARIO/faturamento-dashboard.git

# Push
git push -u origin main
```

---

## Passo 2: Configurar no EasyPanel

### 2.1 Acessar EasyPanel
1. Acesse: `https://seu-vps-ip:3000` ou o domínio do EasyPanel
2. Faça login

### 2.2 Criar novo projeto
1. Clique em **"Create Project"**
2. Nome: `faturamento-dashboard`

### 2.3 Criar novo serviço
1. Dentro do projeto, clique em **"+ Create Service"**
2. Selecione **"App"**
3. Escolha **"GitHub"** (ou GitLab)
4. Conecte sua conta GitHub se ainda não estiver
5. Selecione o repositório `faturamento-dashboard`

### 2.4 Configurar Build
Na aba **"Build"**:
- **Build Type:** `Dockerfile`
- **Dockerfile Path:** `Dockerfile`
- **Context:** `.`

### 2.5 Configurar Variáveis de Ambiente (Build Args)
Na aba **"Environment"** ou **"Build Args"**:

```
VITE_GOOGLE_SHEETS_URL=https://docs.google.com/spreadsheets/d/SEU_ID/pub?output=csv
```

### 2.6 Configurar Domínio
Na aba **"Domains"**:
1. Clique em **"Add Domain"**
2. Adicione seu domínio: `faturamento.seudominio.com`
3. Ative **HTTPS** (Let's Encrypt)

### 2.7 Configurar Porta
Na aba **"Network"** ou **"Ports"**:
- **Container Port:** `80`
- **Protocol:** `HTTP`

---

## Passo 3: Deploy

1. Clique em **"Deploy"**
2. Aguarde o build (pode levar 2-5 minutos)
3. Verifique os logs se houver erro

---

## Atualizações Futuras

### Opção 1: Auto-deploy (recomendado)
1. No EasyPanel, ative **"Auto Deploy"** nas configurações
2. Cada push para `main` dispara um novo deploy

### Opção 2: Deploy manual
1. Faça push das alterações para o GitHub
2. No EasyPanel, clique em **"Rebuild"**

---

## Troubleshooting

### Build falha
- Verifique os logs de build no EasyPanel
- Certifique-se que o Node.js no Dockerfile é versão 20+

### Site não carrega
- Verifique se a porta 80 está exposta
- Verifique os logs do container

### Dados não carregam
- Verifique se `VITE_GOOGLE_SHEETS_URL` está correto
- A planilha precisa estar **publicada na web**

### CORS Error
- Certifique-se que a planilha está publicada como CSV público

---

## Estrutura de Arquivos para Deploy

```
faturamento-dashboard/
├── Dockerfile          # Configuração do container
├── nginx.conf          # Configuração do servidor web
├── .dockerignore       # Arquivos ignorados no build
├── package.json
├── src/
└── ...
```

---

## Configuração da Planilha Google

1. Abra sua planilha no Google Sheets
2. **Arquivo** → **Compartilhar** → **Publicar na web**
3. Selecione a aba com os dados
4. Formato: **CSV**
5. Copie o link gerado
6. Use esse link como `VITE_GOOGLE_SHEETS_URL`
