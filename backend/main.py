# backend/main.py
from fastapi import FastAPI, Depends, HTTPException, Security, Request
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

# Importações locais corrigidas (sem o prefixo 'backend.')
from models import get_engine, Product
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lógica de inicialização...
    logging.info("Aplicação iniciada.")
    yield
    # Lógica de finalização...
    logging.info("Aplicação encerrada.")

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
async def tool_call(request: Request, api_key: str = Depends(get_api_key)):
    """Executa uma ferramenta MCP"""
    try:
        payload = await request.json()
        tool_name = payload.get("tool_name")
        params = payload.get("params", {})
        query = params.get("query", "").strip()
        page = params.get("page", 1)

        engine = get_engine()
        with Session(engine) as db:
            if tool_name == "search_products":
                if not query:
                    return {"tools": [{"items": [], "page": 1, "has_more": False}]}

                query_clean = unidecode(query.lower())
                limit = 3
                offset = (page - 1) * limit

                stopwords = {
                    "de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "com", "para",
                    "quero", "queria", "gostaria", "ver", "me", "mostra", "mostrar", "um", "uma", "uns", "umas",
                    "algum", "alguma", "alguns", "algumas", "opcao", "opcoes", "opçao", "opçoes"
                }
                tokens = [word for word in re.split(r'[\\s,/-]+', query_clean) if word and word not in stopwords]

                if not tokens:
                    return {"tools": [{"items": [], "page": 1, "has_more": False}]}

                where_clauses = []
                ranking_clauses = []
                params = {"limit": limit + 1, "offset": offset}

                for i, token in enumerate(tokens):
                    # Coleta as condições WHERE para este token (plural e singular)
                    where_conditions = []
                    
                    # Lógica para o token original (potencialmente plural)
                    p_like = f'p_like_{i}'
                    params[p_like] = f"%{token}%"
                    where_conditions.append(f'immutable_unaccent("NOMEFANTASIA") ILIKE :{p_like}')
                    where_conditions.append(f'immutable_unaccent(group_description) ILIKE :{p_like}')

                    # Lógica para o singular, se aplicável
                    singular = None
                    if token.endswith('s') and len(token) > 3:
                        if token.endswith('is') and len(token) > 4: # ex: enxovais -> enxoval
                            singular = token[:-2] + 'l'
                        elif token.endswith('es') and len(token) > 4: # ex: meses -> mes
                            singular = token[:-2]
                        else: # ex: toalhas -> toalha
                            singular = token[:-1]
                        
                        if singular:
                            s_like = f's_like_{i}'
                            params[s_like] = f"%{singular}%"
                            where_conditions.append(f'immutable_unaccent("NOMEFANTASIA") ILIKE :{s_like}')
                            where_conditions.append(f'immutable_unaccent(group_description) ILIKE :{s_like}')
                    
                    # Cada token (com suas variações) forma um grupo de condições OR
                    where_clauses.append(f"({ ' OR '.join(where_conditions) })")

                    # Lógica de Ranking para este token
                    p_start = f'p_start_{i}'
                    params[p_start] = f"{token}%"
                    
                    ranking_parts = [
                        f'WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :{p_start} THEN 2',
                        f'WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :{p_like} THEN 3',
                        f'WHEN immutable_unaccent(group_description) ILIKE :{p_like} THEN 4'
                    ]
                    
                    if singular:
                        s_start = f's_start_{i}'
                        params[s_start] = f"{singular}%"
                        s_like = f's_like_{i}' # já está nos params
                        ranking_parts.insert(0, f'WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :{s_start} THEN 1')
                        ranking_parts.insert(3, f'WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :{s_like} THEN 3')
                        ranking_parts.insert(5, f'WHEN immutable_unaccent(group_description) ILIKE :{s_like} THEN 4')

                    ranking_parts.append('ELSE 5')
                    ranking_clauses.append(f"(CASE {' '.join(ranking_parts)} END)")

                # Combina todas as cláusulas
                full_where_clause = " AND ".join(where_clauses)
                full_ranking_logic = " + ".join(ranking_clauses)
                
                sql_query = text(f"""
                    SELECT 
                        "CODIGO", "NOMEFANTASIA", "PRECO1", "PRECO2"
                    FROM 
                        products
                    WHERE {full_where_clause}
                    ORDER BY
                        {full_ranking_logic} ASC,
                        "NOMEFANTASIA" ASC
                    LIMIT :limit OFFSET :offset
                """)

                results = db.execute(sql_query, params).fetchall()
                
                has_more = len(results) > limit
                products_to_return = results[:limit]

                items = [
                    {
                        "code": p.CODIGO,
                        "name": p.NOMEFANTASIA,
                        "price": float(p.PRECO2) if p.PRECO2 is not None else 0.0,
                        "price_cash": float(p.PRECO1) if p.PRECO1 is not None else 0.0,
                    }
                    for p in products_to_return
                ]

                return {"tools": [{"items": items, "page": page, "has_more": has_more}]}

            return {"tools": []}

    except Exception as e:
        logger.error(f"Erro em tool_call: {e}", exc_info=True)
        # Retorna uma resposta de erro estruturada
        return {
            "tools": [
                {
                    "error": f"Ocorreu um erro interno ao processar a ferramenta: {e}"
                }
            ]
        }