# backend/main.py
from fastapi import (
    FastAPI, 
    HTTPException, 
    Security, 
    Request
)
from fastapi.responses import JSONResponse
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

# Importações locais corrigidas (sem o prefixo 'backend.')
from models import Product, get_engine # Importa a nova função
from sqlalchemy.orm import sessionmaker
from tga_client import sync_products, sync_groups

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
API_KEY_HEADER = APIKeyHeader(name="X-API-KEY")

# Cria a instância da aplicação FastAPI com o novo lifespan
app = FastAPI(
    title="MCP TGA Server",
    version="1.0.0",
)

# Dependência de Autenticação
async def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if SERVER_API_KEY and api_key == SERVER_API_KEY:
        return api_key
    else:
        raise HTTPException(status_code=403, detail="Could not validate credentials")

# Agenda de sincronização (a cada 6h)
scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("interval", hours=6)
def scheduled_sync():
    """
    Job agendado para sincronizar grupos e produtos periodicamente.
    Cria sua própria sessão de banco de dados para garantir a independência.
    """
    logger.info("Iniciando sincronização agendada de grupos e produtos...")
    engine = get_engine()
    SessionLocal_sync = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal_sync()
    try:
        sync_groups(db)
        sync_products(db)
        logger.info("Sincronização agendada concluída com sucesso.")
    except Exception as e:
        logger.error(f"Erro na sincronização agendada: {e}", exc_info=True)
    finally:
        db.close()

# @app.on_event("startup")
# async def startup_event():
#     logger.info("Iniciando servidor MCP")
    
#     # Inicia a sincronização em segundo plano
#     asyncio.create_task(sync_products_from_tga())
    
#     # Inicia o agendador
#     scheduler.start()
    
#     logger.info("Servidor MCP iniciado. Sincronização em segundo plano.")

@app.get("/health", status_code=200)
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

    # Cria uma nova sessão de DB para cada chamada, garantindo a conexão correta
    engine = get_engine()
    SessionLocal_request = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal_request()

    try:
        if tool_name == "search_products":
            if not query:
                return {"tools": [{"items": [], "page": 1, "has_more": False}]}

            # Lógica de busca com ILIKE e ranking explícito para máxima relevância
            query_clean = unidecode(query.lower())
            limit = 3
            offset = (page - 1) * limit

            stopwords = {"de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "com", "para"}
            tokens = [word for word in re.split(r'[\\s,/-]+', query_clean) if word and word not in stopwords]

            if not tokens:
                return {"tools": [{"items": [], "page": 1, "has_more": False}]}

            where_clauses = []
            ranking_clauses = []
            params = {"limit": limit + 1, "offset": offset}

            for i, token in enumerate(tokens):
                singular_token = token[:-1] if token.endswith('s') and len(token) > 3 else token
                
                # Parâmetros para ILIKE
                params[f'p_like_{i}'] = f"%{token}%"
                params[f's_like_{i}'] = f"%{singular_token}%"
                params[f'p_start_{i}'] = f"{token}%"
                params[f's_start_{i}'] = f"{singular_token}%"

                # Cláusula WHERE: precisa corresponder de alguma forma
                where_clauses.append(f"""
                    (immutable_unaccent("NOMEFANTASIA") ILIKE :p_like_{i} OR
                     immutable_unaccent("NOMEFANTASIA") ILIKE :s_like_{i} OR
                     immutable_unaccent(group_description) ILIKE :p_like_{i} OR
                     immutable_unaccent(group_description) ILIKE :s_like_{i})
                """)

                # Cláusula de Ranking: atribui pontos com base na relevância
                ranking_clauses.append(f"""
                    (CASE
                        WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :s_start_{i} THEN 1
                        WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :p_start_{i} THEN 1
                        WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :s_like_{i} THEN 2
                        WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :p_like_{i} THEN 2
                        WHEN immutable_unaccent(group_description) ILIKE :s_like_{i} THEN 3
                        WHEN immutable_unaccent(group_description) ILIKE :p_like_{i} THEN 3
                        ELSE 4
                    END)
                """)
            
            full_where_clause = " AND ".join(where_clauses)
            full_ranking_logic = " + ".join(ranking_clauses)

            sql_query = f"""
                SELECT *
                FROM products
                WHERE {full_where_clause}
                ORDER BY ({full_ranking_logic}) ASC, "NOMEFANTASIA" ASC
                LIMIT :limit OFFSET :offset;
            """

            results_proxy = db.execute(sql_text(sql_query), params)
            results = results_proxy.mappings().all()

            # Lógica de paginação
            has_more = len(results) > limit
            page_items = results[:limit]

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