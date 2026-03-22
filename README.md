# 🍔 Cardápio Digital

App de cardápio digital com pedidos via WhatsApp e painel do admin.
Banco de dados: **PostgreSQL**

---

## 💻 Rodar localmente com Docker

### Pré-requisito
Instale o **Docker Desktop**: [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)

### Subir o projeto
```bash
docker-compose up --build
```

Aguarde as mensagens de inicialização e acesse:

| Tela         | URL                         |
|--------------|-----------------------------|
| Cardápio     | http://localhost:5000       |
| Painel admin | http://localhost:5000/admin |

### Parar o projeto
```bash
docker-compose down
```

### Parar e apagar o banco (reset completo)
```bash
docker-compose down -v
```

---

## 🚀 Publicar no Railway

### 1. Suba para o GitHub
1. Crie uma conta em [github.com](https://github.com)
2. Crie um repositório novo e faça upload de todos os arquivos

### 2. Crie o projeto no Railway
1. Acesse [railway.app](https://railway.app) e faça login com GitHub
2. Clique em **"New Project" → "Deploy from GitHub repo"**
3. Selecione o repositório

### 3. Adicione o PostgreSQL
1. Clique em **"+ New" → "Database" → "Add PostgreSQL"**
2. O Railway adiciona a variável `DATABASE_URL` automaticamente ✅

### 4. Gere o domínio público
1. Clique no serviço → **"Settings" → "Networking" → "Generate Domain"**
2. Sua URL pública estará pronta!

---

## Estrutura

```
cardapio/
├── backend/
│   ├── app.py          # Servidor Flask
│   └── database.py     # Banco PostgreSQL
├── frontend/
│   └── templates/
│       ├── cardapio.html
│       └── admin.html
├── uploads/
├── Dockerfile
├── docker-compose.yml  # Para rodar localmente
├── Procfile            # Para o Railway
└── railway.json
```
