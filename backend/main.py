# backend/main.py
from fastapi import FastAPI, HTTPException, Security, BackgroundTasks
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Any, Dict
import asyncio
import logging
import sys
import json
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text as sql_text
from unidecode import unidecode
import re
import os
from dotenv import load_dotenv
from fastapi_another_jwt_auth.exceptions import AuthJWTException
from backend.models import Product, SessionLocal
from backend.tga_client import sync_products

load_dotenv()

# Importações locais
from models import Product, SessionLocal
from tga_client import sync_products_from_tga

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

# Variáveis de Ambiente e Segurança
SERVER_API_KEY = os.getenv("SERVER_API_KEY")
API_KEY_HEADER = APIKeyHeader(name="X-API-Key")

app = FastAPI(title="MCP Server - Loja TGA")

# Dependência de Autenticação
async def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if SERVER_API_KEY and api_key == SERVER_API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# Agenda de sincronização (a cada 6h)
scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("interval", hours=6)
async def scheduled_sync():
    await sync_products_from_tga()

@app.on_event("startup")
async def startup_event():
    logger.info("Iniciando servidor MCP")
    
    # Inicia a sincronização em segundo plano
    asyncio.create_task(sync_products_from_tga())
    
    # Inicia o agendador
    scheduler.start()
    
    logger.info("Servidor MCP iniciado. Sincronização em segundo plano.")

@app.get("/health")
def health_check():
    """
    Verificação de saúde simples. Não depende do banco de dados.
    Se a API está respondendo, está 'saudável'.
    """
    return {"status": "ok"}

@app.get("/tools")
async def list_tools():
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
async def tool_call(request: ToolCallRequest, api_key: str = Security(get_api_key)):
    """Executa uma ferramenta MCP"""
    tool_name = request.tool_name
    params = request.params
    query = params.get("query", "").strip()
    page = max(1, params.get("page", 1))

    try:
        if tool_name == "search_products":
            if not query:
                return {
                    "tools": [
                        {"response1": "Por favor, informe um termo de busca."}
                    ]
                }

            db = SessionLocal()

            # Normaliza a busca e extrai tokens (palavras) úteis, removendo stopwords.
            query_clean = unidecode(query.lower())
            stopwords = {
                "de", "do", "da", "dos", "das", "o", "a", "os", "as", "com",
                "um", "uma", "uns", "umas", "pra", "para", "ver", "quero",
                "algum", "alguma", "alguns", "algumas", "vermos", "mostrar", "tem"
            }
            raw_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ0-9]+", query)]
            tokens = [t for t in raw_tokens if t not in stopwords and len(t) >= 2]

            if not tokens and len(query_clean) >= 2:
                tokens = [query_clean]

            # Constrói cláusulas de busca para cada token
            where_clauses = []
            params = {
                "query": query,
                "limit": 4,  # Busca um extra para saber se há mais páginas
                "offset": (page - 1) * 3
            }
            for i, token in enumerate(tokens):
                param_name = f"t{i}"
                main_token = token[:-1] if token.endswith("s") and len(token) > 3 else token
                params[param_name] = f"%{main_token}%"
                where_clauses.append(f'immutable_unaccent("NOMEFANTASIA") ILIKE :{param_name}')

            # Query SQL com CTEs para combinar estratégias e rankear resultados
            # 1. Busca por correspondência de todos os tokens (rank alto)
            # 2. Busca por texto completo (rank de relevância)
            # 3. Busca por similaridade (rank para erros de digitação)
            and_clause = " AND ".join(where_clauses) if where_clauses else "1=0"
            sql_query = f"""
            WITH results AS (
                SELECT *, 1.5 AS rank
                FROM products
                WHERE {and_clause}
                UNION ALL
                SELECT *, ts_rank(search_vector, plainto_tsquery('portuguese', :query)) AS rank
                FROM products
                WHERE search_vector @@ plainto_tsquery('portuguese', :query)
                UNION ALL
                SELECT *, similarity(immutable_unaccent("NOMEFANTASIA"), immutable_unaccent(:query)) AS rank
                FROM products
                WHERE similarity(immutable_unaccent("NOMEFANTASIA"), immutable_unaccent(:query)) > 0.15
            ),
            ranked_deduped AS (
                SELECT "CODPRD", MAX(rank) as max_rank
                FROM results
                GROUP BY "CODPRD"
            )
            SELECT p.*
            FROM products p
            JOIN ranked_deduped rd ON p."CODPRD" = rd."CODPRD"
            ORDER BY rd.max_rank DESC, p."NOMEFANTASIA" ASC
            LIMIT :limit OFFSET :offset;
            """

            results = db.query(Product).from_statement(sql_text(sql_query)).params(**params).all()
            db.close()

            if not results:
                return {
                    "tools": [
                        {
                            "items": [],
                            "page": page,
                            "has_more": False
                        }
                    ]
                }

            has_more = len(results) == 4
            page_items = results[:3]

            # Resposta estruturada
            structured_response = {
                "items": [
                    {
                        "code": prod.CODPRD,
                        "name": prod.NOMEFANTASIA,
                        "price": float(f"{prod.PRECO2:.2f}") if prod.PRECO2 is not None else 0.0,
                        "price_cash": float(f"{prod.PRECO1:.2f}") if prod.PRECO1 is not None else 0.0,
                    }
                    for prod in page_items
                ],
                "page": page,
                "has_more": has_more,
            }

            return {"tools": [structured_response]}

        else:
            raise HTTPException(status_code=404, detail="Ferramenta não encontrada")

    except Exception as e:
        logger.error(f"Erro em tool_call: {e}")
        # Retorna uma resposta de erro amigável e estruturada
        error_response = {
            "items": [],
            "page": 1,
            "has_more": False,
            "error": "Desculpe, não consegui buscar os produtos no momento. Tente novamente em instantes."
        }
        return {"tools": [error_response]}