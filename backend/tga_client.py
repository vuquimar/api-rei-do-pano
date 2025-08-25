# backend/tga_client.py
import httpx
import os
from models import Product, SessionLocal, ProductGroup
from dotenv import load_dotenv
from datetime import datetime, timezone
import json
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import logging
from typing import Optional
from sqlalchemy.orm import Session
import traceback

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

def sync_groups(db: Session):
    """
    Sincroniza os grupos de produtos da API TGA para o banco de dados local.
    """
    if not API_BASE or not API_KEY:
        logger.error("[ERRO GRUPOS] As variáveis de ambiente da API TGA não estão configuradas.")
        return
        
    logger.info("▶️ Iniciando sincronização de GRUPOS de produtos...")
    total_count = 0
    page = 1
    limit = 100

    try:
        headers = {"X-API-Key": API_KEY}
        endpoint = f"{API_BASE}/v1/grupos"

        while True:
            params = {"page": page, "limit": limit}
            logger.info(f"Buscando grupos da TGA: página {page}...")
            
            response = httpx.get(endpoint, headers=headers, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json().get("data", [])

            if not data:
                logger.info("Nenhum grupo novo encontrado. Finalizando busca na TGA.")
                break

            for item in data:
                group = ProductGroup(
                    CODGRUPO=item["CODGRUPO"],
                    DESCRICAO=item["DESCRICAO"],
                )
                db.merge(group)
                total_count += 1

            db.commit()

            if len(data) < limit:
                break
            page += 1

        logger.info(f"✅ Sincronização de grupos bem-sucedida. {total_count} grupos sincronizados.")

    except httpx.RequestError as e:
        logger.warning(f"[AVISO GRUPOS] Não foi possível conectar à API TGA: {e}. O servidor continuará com os dados existentes.")
        db.rollback()
    except httpx.HTTPStatusError as e:
        logger.warning(f"[AVISO GRUPOS] Falha na chamada à API TGA: {e.response.status_code}. O servidor continuará com os dados existentes.")
        db.rollback()
    except Exception as e:
        logger.error(f"[ERRO GRUPOS] Falha inesperada na sincronização: {e}")
        db.rollback()


def sync_products(db: Session):
    """
    Sincroniza os produtos da API TGA para o banco de dados local.
    Busca todos os produtos, lidando com paginação.
    """
    if not API_BASE or not API_KEY:
        logger.error("[ERRO PRODUTOS] As variáveis de ambiente da API TGA não estão configuradas.")
        return

    logger.info("▶️ Iniciando sincronização COMPLETA de produtos...")
    
    # 1. Carregar todos os grupos do banco de dados para um mapa.
    group_map = {group.CODGRUPO: group.DESCRICAO for group in db.query(ProductGroup).all()}
    logger.info(f"Mapa com {len(group_map)} grupos carregado do banco de dados.")

    total_products = 0
    page = 1
    
    try:
        with httpx.Client() as client:
            while True:
                logger.info(f"Buscando produtos da TGA: página {page}...")
                response = client.get(
                    f"{API_BASE}/v1/produtos",
                    headers={"X-API-Key": API_KEY},
                    params={"page": page, "limit": 100},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                if not data.get("data", {}).get("items"):
                    logger.info(f"✅ Sincronização bem-sucedida. {total_products} produtos no total foram sincronizados.")
                    break

                products_to_upsert = []
                for item in data.get("data", {}).get("items", []):
                    price1 = item.get("PRECO1") if item.get("PRECO1") is not None else 0.0
                    price2 = item.get("PRECO2") if item.get("PRECO2") is not None else 0.0

                    # Mapeamento explícito para evitar erros com campos inesperados da API
                    product_data = {
                        "CODPRD": item.get("CODPRD"),
                        "NOMEFANTASIA": item.get("NOMEFANTASIA"),
                        "PRECO1": price1,
                        "PRECO2": price2,
                        "CODGRUPO": item.get("CODGRUPO"),
                        "group_description": group_map.get(item.get("CODGRUPO"), "")
                    }
                    products_to_upsert.append(product_data)
                
                # Otimização: Fazer upsert em lote para a página atual
                if products_to_upsert:
                    for p_data in products_to_upsert:
                        existing_product = db.query(Product).filter(Product.CODPRD == p_data['CODPRD']).first()
                        if existing_product:
                            existing_product.NOMEFANTASIA = p_data['NOMEFANTASIA']
                            existing_product.PRECO1 = p_data['PRECO1']
                            existing_product.PRECO2 = p_data['PRECO2']
                            existing_product.CODGRUPO = p_data['CODGRUPO']
                            existing_product.group_description = p_data['group_description']
                        else:
                            db.add(Product(**p_data))
                    
                    db.commit()

                total_products += len(products_to_upsert)
                page += 1
    
    except httpx.RequestError as e:
        logger.warning(f"⚠️ [AVISO PRODUTOS] A API da TGA parece estar offline ou inacessível. Erro: {e}. A aplicação continuará usando os dados da última sincronização bem-sucedida.")
    except Exception as e:
        logger.error(f"[ERRO PRODUTOS] Falha inesperada na sincronização: {e}")
        db.rollback() # Garante que a transação seja desfeita em caso de erro
        traceback.print_exc()