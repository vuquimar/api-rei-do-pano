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

                # Lógica de busca PROFISSIONAL com pg_trgm para tolerância a erros e ranking de relevância
                score_clauses = []
                # O threshold aumenta com o número de palavras, exigindo que mais termos correspondam.
                # 0.25 é um bom ponto de partida para similaridade.
                params = {"limit": limit + 1, "offset": offset, "threshold": 0.25 * len(tokens)} 

                for i, token in enumerate(tokens):
                    plural = token
                    
                    # Lógica de singular aprimorada
                    singular = plural
                    if plural.endswith('s') and len(plural) > 3:
                        if plural.endswith('is'): singular = plural[:-2] + 'l' # enxovais -> enxoval
                        else: singular = plural[:-1] # toalhas -> toalha

                    p_param = f'p_{i}'
                    params[p_param] = plural
                    
                    # A similaridade é calculada contra o nome E a descrição do grupo
                    token_scores = [
                        f"similarity(immutable_unaccent(\"NOMEFANTASIA\"), :{p_param})",
                        # Um match no grupo tem um peso um pouco menor (80%) que no nome
                        f"similarity(immutable_unaccent(coalesce(group_description, '')), :{p_param}) * 0.8"
                    ]
                    
                    # Bônus enorme se o nome do produto COMEÇAR com o termo (alta relevância)
                    p_start_param = f'p_start_{i}'
                    params[p_start_param] = f"{plural}%"
                    token_scores.append(f"(CASE WHEN immutable_unaccent(\"NOMEFANTASIA\") ILIKE :{p_start_param} THEN 1.0 ELSE 0.0 END)")

                    if singular != plural:
                        s_param = f's_{i}'
                        params[s_param] = singular
                        s_start_param = f's_start_{i}'
                        params[s_start_param] = f"{singular}%"
                        
                        token_scores.extend([
                            f"similarity(immutable_unaccent(\"NOMEFANTASIA\"), :{s_param})",
                            f"similarity(immutable_unaccent(coalesce(group_description, '')), :{s_param}) * 0.8",
                            f"(CASE WHEN immutable_unaccent(\"NOMEFANTASIA\") ILIKE :{s_start_param} THEN 1.0 ELSE 0.0 END)"
                        ])
                    
                    # A pontuação para este token é a MAIOR pontuação entre suas variações
                    score_clauses.append(f"GREATEST({', '.join(token_scores)})")

                # A pontuação final é a SOMA das pontuações de cada token.
                # Isso garante que produtos que correspondem a MAIS tokens tenham uma pontuação maior.
                full_score_logic = " + ".join(score_clauses)

                sql_query = text(f"""
                    SELECT "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2"
                    FROM (
                        SELECT
                            "CODPRD", "NOMEFANTASIA", "PRECO1", "PRECO2",
                            ({full_score_logic}) AS relevance_score
                        FROM products
                    ) AS ranked_products
                    WHERE relevance_score > :threshold
                    ORDER BY relevance_score DESC, "NOMEFANTASIA" ASC
                    LIMIT :limit OFFSET :offset
                """)

                results = db.execute(sql_query, params).fetchall()
                
                has_more = len(results) > limit
                products_to_return = results[:limit]

                items = [
                    {
                        "code": p.CODPRD,
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