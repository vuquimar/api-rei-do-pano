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
        # Solução final: tokenização + busca inteligente
        
        # Tokenizar a consulta e remover stopwords
        stopwords = {'e', 'de', 'da', 'do', 'das', 'dos', 'para', 'com', 'em', 'por', 'a', 'o', 'as', 'os'}
        tokens = [t for t in query.lower().strip().split() if t not in stopwords and len(t) > 1]
        
        if not tokens:
            return {"items": [], "page": page, "has_more": False}
        
        # Construir condições de busca para cada token
        conditions = []
        search_params = {
            "page_size": page_size,
            "offset": offset
        }
        
        for i, token in enumerate(tokens):
            # Gerar variações singular/plural para cada token
            singular = token
            plural = token
            
            # Regras de transformação singular/plural
            if token.endswith('is'):  # enxovais -> enxoval
                singular = token[:-2] + 'l'
            elif token.endswith('ns'):  # lençóis -> lençol
                singular = token[:-2] + 'm'
            elif token.endswith('s'):  # toalhas -> toalha
                singular = token[:-1]
            elif token.endswith('l'):  # enxoval -> enxovais
                plural = token[:-1] + 'is'
            elif token.endswith('m'):  # homem -> homens
                plural = token[:-1] + 'ns'
            else:
                plural = token + 's'
            
            # Adicionar parâmetros para todas as variações
            token_param = f"token_{i}"
            singular_param = f"singular_{i}"
            plural_param = f"plural_{i}"
            
            search_params[token_param] = f"%{token}%"
            search_params[singular_param] = f"%{singular}%"
            search_params[plural_param] = f"%{plural}%"
            
            # Condição para este token: buscar original, singular ou plural no nome ou descrição do grupo
            token_condition = f"""(
                immutable_unaccent("NOMEFANTASIA") ILIKE :{token_param} OR
                immutable_unaccent("NOMEFANTASIA") ILIKE :{singular_param} OR
                immutable_unaccent("NOMEFANTASIA") ILIKE :{plural_param} OR
                immutable_unaccent(group_description) ILIKE :{token_param} OR
                immutable_unaccent(group_description) ILIKE :{singular_param} OR
                immutable_unaccent(group_description) ILIKE :{plural_param}
            )"""
            
            conditions.append(token_condition)
        
        # Combinar condições com AND (todos os tokens devem estar presentes)
        where_clause = " AND ".join(conditions)
        
        # Construir a consulta SQL final
        search_sql = text(f"""
            SELECT 
                "CODPRD", 
                "NOMEFANTASIA", 
                "PRECO1", 
                "PRECO2"
            FROM 
                products
            WHERE 
                {where_clause}
            ORDER BY 
                CASE 
                    WHEN immutable_unaccent("NOMEFANTASIA") ILIKE :{tokens[0] + '_exact'} THEN 1
                    ELSE 2
                END,
                "NOMEFANTASIA" ASC
            LIMIT :page_size OFFSET :offset
        """)
        
        # Adicionar parâmetro para ordenação exata
        search_params[tokens[0] + '_exact'] = tokens[0]
        
        # Executar a busca
        results = db.execute(search_sql, search_params).fetchall()

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