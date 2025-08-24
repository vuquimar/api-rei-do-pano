# backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copia requirements e instala dependências globalmente
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código do app
COPY . .

# Permissões e usuário não-root
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expõe a porta que o app vai rodar
EXPOSE 8080

# Comando para rodar a aplicação em produção
# 1. Aplica as migrações do banco de dados
# 2. Inicia o servidor Gunicorn com workers Uvicorn
CMD ["sh", "-c", "alembic upgrade head && gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:8080"]