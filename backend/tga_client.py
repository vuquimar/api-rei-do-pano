# backend/tga_client.py
import httpx
import os
from models import Product, SessionLocal
from dotenv import load_dotenv
from datetime import datetime, timezone
import json
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import logging

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_BASE = os.getenv("API_BASE_URL")
API_KEY = os.getenv("API_KEY")
HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}

LAST_SYNC_FILE = "last_sync.json"

def get_last_sync():
    try:
        with open(LAST_SYNC_FILE, "r") as f:
            return json.load(f).get("last_sync")
    except:
        return None

def save_last_sync():
    with open(LAST_SYNC_FILE, "w") as f:
        json.dump({"last_sync": datetime.now(timezone.utc).isoformat()}, f)

def sync_products():
    """
    Sincroniza TODOS os produtos da API TGA para o banco de dados local,
    lidando com a paginação da API de origem.
    """
    logger.info("▶️ Iniciando sincronização COMPLETA de produtos...")
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("[ERRO] Variável de ambiente DATABASE_URL não encontrada para sincronização.")
        return

    engine = create_engine(db_url) # Reutiliza a função de models.py para consistência
    SessionLocal_sync = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal_sync()

    total_count = 0
    page = 1
    limit = 100 # Quantidade de produtos a buscar por chamada na API TGA

    try:
        api_base_url = os.getenv("API_BASE_URL")
        api_key = os.getenv("API_KEY")

        if not all([api_base_url, api_key]):
            logger.error("Variáveis de ambiente API_BASE_URL ou API_KEY não configuradas.")
            return

        headers = {"X-API-Key": api_key}
        endpoint = f"{api_base_url}/v1/produtos"
        
        while True:
            params = {"page": page, "limit": limit}
            logger.info(f"Buscando produtos da TGA: página {page}...")
            
            response = httpx.get(endpoint, headers=headers, params=params, timeout=30.0)
            response.raise_for_status()
            
            data = response.json().get("data", [])
            
            if not data:
                logger.info("Nenhum produto novo encontrado. Finalizando busca na TGA.")
                break

            for item in data:
                product = Product(
                    CODPRD=item["CODPRD"],
                    NOMEFANTASIA=item["NOMEFANTASIA"],
                    PRECO2=float(item.get("PRECO2") or 0.0),
                    PRECO1=float(item.get("PRECO1") or 0.0),
                    SALDOGERALFISICO=float(item.get("SALDOGERALFISICO", 0)),
                    CODGRUPO=item.get("CODGRUPO"),
                    CODBARRAS=item.get("CODBARRAS")
                )
                db.merge(product)
                total_count += 1
            
            db.commit()
            
            # Se a API retornou menos que o limite, significa que é a última página
            if len(data) < limit:
                break
                
            page += 1

        logger.info(f"✅ Sincronização bem-sucedida. {total_count} produtos no total foram sincronizados.")

    except httpx.HTTPStatusError as e:
        logger.error(f"[ERRO] Falha na chamada à API TGA: {e.response.status_code} - {e.response.text}")
        db.rollback()
    except Exception as e:
        logger.error(f"[ERRO] Falha na sincronização: {e}")
        db.rollback()
    finally:
        db.close()
        logger.info("🔄 Sincronização concluída.")