FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o projeto
COPY . .

# Cria pasta de uploads
RUN mkdir -p /app/uploads

EXPOSE 5000

CMD ["gunicorn", "--chdir", "backend", "app:app", "--bind", "0.0.0.0:5000", "--timeout", "120"]
