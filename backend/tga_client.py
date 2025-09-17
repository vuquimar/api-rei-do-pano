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
import time

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

def get_tga_data_with_retry(url: str, params: dict, retries=3, delay=5):
    """Faz uma requisição GET para a API TGA com lógica de retry."""
    for attempt in range(retries):
        try:
            with httpx.Client() as client:
                response = client.get(
                    url,
                    headers=HEADERS,
                    params=params,
                    timeout=60.0,
                )
                response.raise_for_status()
                return response.json().get("data", [])
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"Tentativa {attempt + 1} de {retries} falhou: {e}. Tentando novamente em {delay}s...")
            if attempt + 1 == retries:
                logger.error(f"Todas as {retries} tentativas falharam. Abortando a requisição para {url}.")
                raise
            time.sleep(delay)

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
    Sincroniza os produtos da API TGA para o banco de dados local,
    incluindo a remoção de produtos que não existem mais na API de origem.
    """
    if not API_BASE or not API_KEY:
        logger.error("[ERRO PRODUTOS] As variáveis de ambiente da API TGA não estão configuradas.")
        return

    logger.info("▶️ Iniciando sincronização COMPLETA de produtos...")
    
    try:
        # --- ETAPA 1: Obter todos os códigos de produto da API TGA ---
        all_tga_product_codes = set()
        page = 1
        limit = 100  # REDUZIDO: Diminuir o limite para evitar timeouts da TGA
        logger.info("Buscando todos os códigos de produto da API TGA...")
        
        while True:
            params = {"page": page, "limit": limit, "fields": "CODPRD"}
            items = get_tga_data_with_retry(f"{API_BASE}/v1/produtos", params)
            
            if not items:
                break
            
            for item in items:
                # BLINDAGEM: Garante que o item é um dicionário e tem a chave esperada
                if isinstance(item, dict) and 'CODPRD' in item:
                    all_tga_product_codes.add(item['CODPRD'])
                else:
                    logger.warning(f"Item malformado recebido da API TGA e ignorado: {item}")
            
            page += 1
        logger.info(f"Encontrados {len(all_tga_product_codes)} produtos na API TGA.")

        # --- ETAPA 2: Obter todos os códigos de produto do banco de dados local ---
        local_product_codes = {p.CODPRD for p in db.query(Product.CODPRD).all()}
        logger.info(f"Encontrados {len(local_product_codes)} produtos no banco de dados local.")

        # --- ETAPA 3: Determinar produtos a serem excluídos ---
        products_to_delete = local_product_codes - all_tga_product_codes
        if products_to_delete:
            logger.info(f"Deletando {len(products_to_delete)} produtos que não existem mais na TGA...")
            db.query(Product).filter(Product.CODPRD.in_(products_to_delete)).delete(synchronize_session=False)
            db.commit()
        else:
            logger.info("Nenhum produto para deletar.")

        # --- ETAPA 4: Sincronizar (adicionar/atualizar) produtos ---
        group_map = {group.CODGRUPO: group.DESCRICAO for group in db.query(ProductGroup).all()}
        logger.info(f"Mapa com {len(group_map)} grupos carregado. Iniciando upsert...")
        
        total_products_synced = 0
        page = 1
        limit = 100 # Manter o limite consistente
        
        while True:
            logger.info(f"Buscando e atualizando produtos da TGA: página {page}...")
            params = {"page": page, "limit": limit}
            items = get_tga_data_with_retry(f"{API_BASE}/v1/produtos", params)

            if not items:
                logger.info(f"✅ Sincronização bem-sucedida. {total_products_synced} produtos foram adicionados/atualizados.")
                    break

                # Mapeia os produtos da página atual para um dicionário
                products_from_tga = {
                    item.get("CODPRD"): item for item in items if item.get("CODPRD")
                }
                
                # Busca em lote todos os produtos existentes no banco de dados para a página atual
                existing_products = db.query(Product).filter(Product.CODPRD.in_(products_from_tga.keys())).all()
                existing_products_map = {p.CODPRD: p for p in existing_products}

                for codprd, item_data in products_from_tga.items():
                    existing_product = existing_products_map.get(codprd)
                    
                    product_details = {
                        "NOMEFANTASIA": item_data.get("NOMEFANTASIA"),
                        "PRECO1": item_data.get("PRECO1", 0.0),
                        "PRECO2": item_data.get("PRECO2", 0.0),
                        "CODGRUPO": item_data.get("CODGRUPO"),
                        "group_description": group_map.get(item_data.get("CODGRUPO"), "")
                    }

                    if existing_product:
                        # Se o produto existe, atualiza os campos
                        existing_product.NOMEFANTASIA = product_details["NOMEFANTASIA"]
                        existing_product.PRECO1 = product_details["PRECO1"]
                        existing_product.PRECO2 = product_details["PRECO2"]
                        existing_product.CODGRUPO = product_details["CODGRUPO"]
                        existing_product.group_description = product_details["group_description"]
                    else:
                        # Se não existe, cria um novo
                        new_product = Product(CODPRD=codprd, **product_details)
                        db.add(new_product)

                db.commit()
                total_products_synced += len(items)
                page += 1
    
    except httpx.RequestError as e:
        logger.warning(f"⚠️ [AVISO PRODUTOS] A API da TGA parece estar offline ou inacessível. Erro: {e}. A aplicação continuará usando os dados da última sincronização bem-sucedida.")
        db.rollback()
    except Exception as e:
        logger.error(f"[ERRO PRODUTOS] Falha inesperada na sincronização: {e}")
        db.rollback()
        traceback.print_exc()