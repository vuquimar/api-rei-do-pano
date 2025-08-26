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

# Importações locais corrigidas (sem o prefixo 'backend.')
from models import get_engine, Product, get_db
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
        # Remover palavras comuns que não ajudam na busca
        stopwords = {'e', 'de', 'da', 'do', 'das', 'dos', 'para', 'com', 'em', 'por'}
        
        # Tokenizar a consulta e remover stopwords
        tokens = [t for t in query.lower().strip().split() if t not in stopwords and len(t) > 1]
        
        if not tokens:
            return {"items": [], "page": page, "has_more": False}
        
        # Abordagem em duas etapas: primeiro busca exata, depois por similaridade
        
        # Etapa 1: Busca direta por correspondência exata ou contém
        direct_query = text("""
            SELECT 
                p."CODPRD", 
                p."NOMEFANTASIA", 
                p."PRECO1", 
                p."PRECO2",
                1.0 AS match_score  -- Pontuação máxima para correspondências diretas
            FROM 
                products p
            WHERE 
                immutable_unaccent(p."NOMEFANTASIA") ILIKE :contains_query
            ORDER BY 
                p."NOMEFANTASIA" ASC
            LIMIT :page_size OFFSET :offset
        """)
        
        # Parâmetros para busca direta
        direct_params = {
            "contains_query": f"%{query.lower().strip()}%",
            "page_size": page_size,
            "offset": offset
        }
        
        # Tentar primeiro com busca direta
        results = db.execute(direct_query, direct_params).fetchall()
        
        # Se não encontrar resultados suficientes, usar similaridade com limiar mais alto
        if len(results) < page_size:
            # Construir a consulta usando similaridade trigram com limiar mais alto
            similarity_query = text("""
                SELECT 
                    p."CODPRD", 
                    p."NOMEFANTASIA", 
                    p."PRECO1", 
                    p."PRECO2",
                    similarity(immutable_unaccent(p."NOMEFANTASIA"), :query_text) AS match_score
                FROM 
                    products p
                WHERE 
                    similarity(immutable_unaccent(p."NOMEFANTASIA"), :query_text) > 0.3
                    AND p."CODPRD" NOT IN :excluded_codes
                ORDER BY 
                    match_score DESC
                LIMIT :remaining_size
            """)
            
            # Extrair códigos dos resultados já obtidos para evitar duplicatas
            excluded_codes = tuple([r.CODPRD for r in results]) if results else ('',)
            
            # Parâmetros para a consulta de similaridade
            similarity_params = {
                "query_text": query.lower().strip(),
                "excluded_codes": excluded_codes,
                "remaining_size": page_size - len(results)
            }
            
            # Executar consulta de similaridade e adicionar aos resultados
            similarity_results = db.execute(similarity_query, similarity_params).fetchall()
            results.extend(similarity_results)
        
        # Se ainda não encontrarmos resultados suficientes, tentar busca por tokens
        if not results:
            keyword_conditions = []
            keyword_params = {"page_size": page_size, "offset": offset}
            
            for i, token in enumerate(tokens):
                token_param = f"token_{i}"
                keyword_params[token_param] = f"%{token}%"
                
                keyword_conditions.append(f"""(
                    immutable_unaccent(p."NOMEFANTASIA") ILIKE :{token_param} OR
                    immutable_unaccent(p.group_description) ILIKE :{token_param}
                )""")
            
            if keyword_conditions:
                where_clause = " OR ".join(keyword_conditions)  # Usando OR para ser mais inclusivo
                fallback_query = text(f"""
                    SELECT p."CODPRD", p."NOMEFANTASIA", p."PRECO1", p."PRECO2", 0.1 as match_score
                    FROM products p
                    WHERE {where_clause}
                    ORDER BY p."NOMEFANTASIA" ASC
                    LIMIT :page_size OFFSET :offset
                """)
                
                results = db.execute(fallback_query, keyword_params).fetchall()

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