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

def sync_groups(db):
    """
    Sincroniza TODOS os grupos de produtos da API TGA para o banco de dados local.
    """
    logger.info("▶️ Iniciando sincronização de GRUPOS de produtos...")
    total_count = 0
    page = 1
    limit = 100

    try:
        if not all([API_BASE, API_KEY]):
            logger.error("Variáveis de ambiente API_BASE_URL ou API_KEY não configuradas.")
            return

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


def sync_products(db: Optional[Session] = None):
    """
    Sincroniza TODOS os produtos da API TGA para o banco de dados local,
    lidando com a paginação da API de origem.
    Pode receber uma sessão de DB externa ou criar a sua própria.
    """
    logger.info("▶️ Iniciando sincronização COMPLETA de produtos...")

    db_session = db
    close_db_after = False
    if not db_session:
        close_db_after = True
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            logger.error("[ERRO] Variável de ambiente DATABASE_URL não encontrada para sincronização.")
            return

        engine = create_engine(db_url)
        SessionLocal_sync = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db_session = SessionLocal_sync()

    total_count = 0
    page = 1
    limit = 100 # Quantidade de produtos a buscar por chamada na API TGA

    try:
        # Pega o mapa de grupos do nosso banco de dados local.
        # Isso assume que sync_groups() já foi executado.
        groups_map = {g.CODGRUPO: g.DESCRICAO for g in db_session.query(ProductGroup).all()}
        logger.info(f"Mapa com {len(groups_map)} grupos carregado do banco de dados.")

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
                group_code = item.get("CODGRUPO")
                product = Product(
                    CODPRD=item["CODPRD"],
                    NOMEFANTASIA=item["NOMEFANTASIA"],
                    PRECO2=float(item.get("PRECO2") or 0.0),
                    PRECO1=float(item.get("PRECO1") or 0.0),
                    SALDOGERALFISICO=float(item.get("SALDOGERALFISICO", 0)),
                    CODGRUPO=group_code,
                    CODBARRAS=item.get("CODBARRAS"),
                    group_description=groups_map.get(group_code) # Adiciona a descrição do grupo
                )
                db_session.merge(product)
                total_count += 1
            
            db_session.commit()
            
            # Se a API retornou menos que o limite, significa que é a última página
            if len(data) < limit:
                break
                
            page += 1

        logger.info(f"✅ Sincronização bem-sucedida. {total_count} produtos no total foram sincronizados.")
        
    except httpx.RequestError as e:
        logger.warning(f"[AVISO PRODUTOS] Não foi possível conectar à API TGA: {e}. O servidor continuará com os dados existentes.")
        db_session.rollback()
    except httpx.HTTPStatusError as e:
        logger.warning(f"[AVISO PRODUTOS] Falha na chamada à API TGA: {e.response.status_code}. O servidor continuará com os dados existentes.")
        db_session.rollback()
    except Exception as e:
        logger.error(f"[ERRO PRODUTOS] Falha inesperada na sincronização: {e}")
        db_session.rollback()

    else:
        logger.error("[ERRO PRODUTOS] As variáveis de ambiente da API TGA não estão configuradas.")
    finally:
        if close_db_after:
            db_session.close()