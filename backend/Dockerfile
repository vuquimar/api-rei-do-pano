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

# Expõe a porta 8080 que o Gunicorn irá usar
EXPOSE 8080

# Comando para rodar migrações e iniciar o servidor de produção
# O 'sh -c' permite rodar múltiplos comandos. O '&&' garante que o gunicorn só rode se o alembic for bem-sucedido.
CMD ["sh", "-c", "alembic upgrade head && gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:8080"]