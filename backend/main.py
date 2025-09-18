# backend/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Security
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from typing import Any, Dict
import logging
import sys
import json
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session
import re
import os
from dotenv import load_dotenv
from unidecode import unidecode
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Importações locais corrigidas (sem o prefixo 'backend.')
from models import get_engine, Product, get_db, SessionLocal
from tga_client import sync_products, sync_groups
from tga_client import run_full_sync_cycle  # usa ciclo com advisory lock

# =============== LOGS EM FORMATO JSON ===============
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        return json.dumps(log_entry)

logger = logging.getLogger("mcp")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
# ===================================================

# Agendador para tarefas de sincronização
scheduler = AsyncIOScheduler()

def run_full_sync():
    """Executa a sincronização completa de grupos e produtos."""
    logger.info("--- Iniciando ciclo de sincronização agendada ---")
    # Passa a usar o ciclo com advisory lock
    run_full_sync_cycle()
    logger.info("--- Ciclo de sincronização agendada finalizado ---")


# Variáveis de Ambiente e Segurança
SERVER_API_KEY = os.getenv("SERVER_API_KEY")
API_KEY_HEADER = APIKeyHeader(name="X-API-KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lógica de inicialização...
    logger.info("Iniciando a aplicação e o agendador de sincronização.")
    
    # Adiciona a tarefa de sincronização recorrente ao agendador
    scheduler.add_job(
        run_full_sync,
        trigger=IntervalTrigger(minutes=30),
        id="sync_job",
        name="Sincronização TGA Recorrente",
        replace_existing=True
    )
    
    # Adiciona uma tarefa para rodar a sincronização uma vez, imediatamente após o início
    scheduler.add_job(
        run_full_sync,
        id="initial_sync_job",
        name="Sincronização TGA Imediata",
        replace_existing=True
    )
    
    # Inicia o agendador (que executa os jobs em background)
    scheduler.start()
    
    logger.info("Aplicação iniciada e pronta para receber requisições. A sincronização inicial foi agendada para rodar em segundo plano.")
    
    yield
    
    # Lógica de finalização...
    logger.info("Encerrando a aplicação e o agendador.")
    scheduler.shutdown()

# Cria a instância da aplicação FastAPI com o novo lifespan
app = FastAPI(
    title="TGA API Server",
    description="Um servidor de API para buscar produtos TGA com capacidades de busca inteligente.",
    version="1.0.0",
    lifespan=lifespan 
)

# Dependência de Autenticação
async def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if SERVER_API_KEY and api_key == SERVER_API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Chave de API inválida ou ausente.")

@app.get("/health")
async def health_check():
    """
    Verificação de saúde simples. Não depende do banco de dados.
    Se a API está respondendo, está 'saudável'.
    """
    return {"status": "ok"}

@app.get("/tools")
async def get_tools_definition(api_key: str = Depends(get_api_key)):
    """Retorna a lista de ferramentas disponíveis"""
    return {
        "tools": [
            {
                "name": "search_products",
                "description": "Busca produtos por nome, código ou código de barras. Retorna 3 por página.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Termo de busca"},
                        "page": {"type": "integer", "description": "Página (1, 2, 3...)", "default": 1},
                        "user_id": {"type": "string", "description": "ID do usuário", "default": "default"}
                    },
                    "required": ["query"]
                }
            }
        ]
    }

class ToolCallRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any]
    user_id: str = "default"

@app.post("/tool_call")
async def tool_call(
    request: ToolCallRequest, 
    db: Session = Depends(get_db), 
    api_key: str = Depends(get_api_key)
):
    query = request.params.get("query", "").strip()
    page = request.params.get("page", 1)
    page_size = 3
    offset = (page - 1) * page_size

    try:
        if not query:
            return {"items": [], "page": page, "has_more": False}

        # Prepara a query removendo stopwords para as buscas mais simples
        stopwords = {'e', 'de', 'da', 'do', 'das', 'dos', 'para', 'com', 'em', 'por', 'a', 'o', 'as', 'os', 'um', 'uma'}
        clean_query_tokens = [t for t in query.lower().strip().split() if t not in stopwords]
        clean_query = " ".join(clean_query_tokens)

        if not clean_query:
            clean_query = query # Fallback se a query só tiver stopwords

        # Constrói a cláusula ILIKE para a nova camada de busca
        ilike_conditions = " AND ".join(
            [f"""immutable_unaccent("NOMEFANTASIA") ILIKE :word_{i}""" for i in range(len(clean_query_tokens))]
        )

        # Estratégia de busca avançada em múltiplas camadas com ranking explícito
        search_sql = text(f"""
            WITH ranked_products AS (
                -- Camada 1: Código exato (rank 1, score máximo)
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 1 AS rank, 10.0 AS score
                FROM products
                WHERE "CODPRD" = :query_code

                UNION ALL

                -- Camada 2: Nome exato (case-insensitive, accent-insensitive)
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 2 AS rank, 8.0 AS score
                FROM products
                WHERE immutable_unaccent("NOMEFANTASIA") = immutable_unaccent(:query)

                UNION ALL

                -- Camada 3: Frase Exata (Literal) contida no nome com ILIKE
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 3 AS rank, 5.0 AS score
                FROM products
                WHERE immutable_unaccent("NOMEFANTASIA") ILIKE immutable_unaccent(:query_like_any_literal)
                
                UNION ALL

                -- Camada 4: Full-Text Search com websearch_to_tsquery
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 4 AS rank,
                       ts_rank_cd(search_vector, websearch_to_tsquery('portuguese', :query)) AS score
                FROM products
                WHERE search_vector @@ websearch_to_tsquery('portuguese', :query)

                UNION ALL

                -- Camada 5: Todas as palavras-chave com ILIKE
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 5 AS rank, 0.8 AS score
                FROM products
                WHERE {ilike_conditions if ilike_conditions else 'FALSE'}

                UNION ALL

                -- Camada 6: ILIKE no início do nome (prefixo)
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 6 AS rank, 0.5 AS score
                FROM products
                WHERE immutable_unaccent("NOMEFANTASIA") ILIKE immutable_unaccent(:query_like_start)

                UNION ALL

                -- Camada 7: Similaridade para typos (fallback)
                SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2", 7 as rank,
                       similarity(immutable_unaccent("NOMEFANTASIA"), immutable_unaccent(:clean_query)) as score
                FROM products
                WHERE similarity(immutable_unaccent("NOMEFANTASIA"), immutable_unaccent(:clean_query)) > 0.15
            ),
            unique_products AS (
                SELECT
                    "CODPRD",
                    "NOMEFANTASIA",
                    "PRECO1",
                    "PRECO2",
                    rank,
                    score,
                    ROW_NUMBER() OVER(PARTITION BY "CODPRD" ORDER BY rank ASC, score DESC) as rn
                FROM ranked_products
            )
            SELECT
                "CODPRD",
                "NOMEFANTASIA",
                "PRECO1",
                "PRECO2"
            FROM unique_products
            WHERE rn = 1
            ORDER BY rank ASC, score DESC, "NOMEFANTASIA" ASC
            LIMIT :page_size OFFSET :offset
        """)

        params = {
            "query": query, # Query original para FTS, que tem seu próprio-tratamento de stopwords
            "clean_query": clean_query, # Query limpa para similaridade
            "query_code": query.upper(),
            "query_like_start": f"{clean_query}%",
            # Frase literal deve usar a query original, mantendo stopwords
            "query_like_any_literal": f"%{query.strip()}%",
            "page_size": page_size,
            "offset": offset,
        }

        # Adiciona os parâmetros para a nova camada de ILIKE
        for i, token in enumerate(clean_query_tokens):
            params[f'word_{i}'] = f"%%{token}%%"

        # Executar a busca
        results = db.execute(search_sql, params).fetchall()

        items = [
            {
                "code": row.CODPRD,
                "name": row.NOMEFANTASIA,
                "price": f"{row.PRECO2:.2f}".replace(".", ","),
                "price_cash": f"{row.PRECO1:.2f}".replace(".", ",")
            }
            for row in results
        ]

        has_more = len(items) == page_size

        return {"items": items, "page": page, "has_more": has_more}

    except Exception as e:
        logger.error(f"Erro ao processar a busca: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a busca.")