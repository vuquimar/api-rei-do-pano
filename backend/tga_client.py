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
from typing import Optional, Tuple, List, Any
from sqlalchemy.orm import Session
import traceback
import time
from sqlalchemy import text as sa_text

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_BASE = os.getenv("API_BASE_URL")
API_KEY = os.getenv("API_KEY")
HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}

LAST_SYNC_FILE = "last_sync.json"

# ---------- Utilidades de Lock Distribuído (garante job único) ----------
SYNC_LOCK_KEY = 823471  # chave arbitrária para pg_advisory_lock

def acquire_sync_lock(db: Session) -> bool:
    try:
        result = db.execute(sa_text("SELECT pg_try_advisory_lock(:k)"), {"k": SYNC_LOCK_KEY}).scalar()
        return bool(result)
    except Exception as e:
        logger.warning(f"Falha ao adquirir advisory lock: {e}")
        return False

def release_sync_lock(db: Session) -> None:
    try:
        db.execute(sa_text("SELECT pg_advisory_unlock(:k)"), {"k": SYNC_LOCK_KEY})
    except Exception:
        pass

# ---------- HTTP Helpers ----------

def get_tga_json_with_retry(url: str, params: dict, retries: int = 3, delay: int = 5) -> Any:
    """Executa GET com retry e retorna o JSON bruto da TGA."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client() as client:
                resp = client.get(url, headers=HEADERS, params=params, timeout=60.0)
                resp.raise_for_status()
                return resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            last_exc = e
            logger.warning(
                f"Tentativa {attempt} de {retries} falhou: {e}. Tentando novamente em {delay}s..."
            )
            time.sleep(delay)
    logger.error(f"Todas as {retries} tentativas falharam. Abortando a requisição para {url}.")
    if last_exc:
        raise last_exc
    raise RuntimeError("Falha desconhecida ao requisitar TGA")


def extract_items_and_total(payload: Any) -> Tuple[List[dict], int]:
    """Extrai lista de itens e total/quantidade total, cobrindo formatos variados."""
    items: List[dict] = []
    total = 0

    if isinstance(payload, list):
        items = [i for i in payload if isinstance(i, dict)]
        return items, total

    if isinstance(payload, dict):
        # Metadados possíveis: quantTotal, total, qtdRegistro
        for key in ("quantTotal", "total", "qtdRegistro"):
            try:
                if key in payload and payload[key] is not None:
                    total = int(payload[key])
                    break
            except Exception:
                pass

        data = payload.get("data")
        if isinstance(data, list):
            items = [i for i in data if isinstance(i, dict)]
            return items, total
        if isinstance(data, dict):
            inner_items = data.get("items")
            if isinstance(inner_items, list):
                items = [i for i in inner_items if isinstance(i, dict)]
                # tenta extrair total interno
                for key in ("quantTotal", "total", "qtdRegistro"):
                    try:
                        if key in data and data[key] is not None:
                            total = int(data[key])
                            break
                    except Exception:
                        pass
                return items, total
        # Alguns provedores usam "items" na raiz
        root_items = payload.get("items")
        if isinstance(root_items, list):
            items = [i for i in root_items if isinstance(i, dict)]
            return items, total

    # Caso dados malformados: retorna vazio
    return items, total

def get_last_sync():
    try:
        with open(LAST_SYNC_FILE, "r") as f:
            return json.load(f).get("last_sync")
    except:
        return None

def save_last_sync():
    with open(LAST_SYNC_FILE, "w") as f:
        json.dump({"last_sync": datetime.now(timezone.utc).isoformat()}, f)

# ===================== Grupos =====================

def sync_groups(db: Session):
    """Sincroniza os grupos de produtos da API TGA."""
    if not API_BASE or not API_KEY:
        logger.error("[ERRO GRUPOS] Variáveis de ambiente da API TGA ausentes.")
        return

    logger.info("▶️ Iniciando sincronização de GRUPOS de produtos...")
    total_count = 0
    page = 1
    limit = 100

    try:
        while True:
            params = {"page": page, "limit": limit}
            logger.info(f"Buscando grupos da TGA: página {page}...")
            payload = get_tga_json_with_retry(f"{API_BASE}/v1/grupos", params)
            items, total = extract_items_and_total(payload)

            if not items:
                logger.info("Nenhum grupo novo encontrado. Finalizando.")
                break

            for item in items:
                if "CODGRUPO" in item and "DESCRICAO" in item:
                    group = ProductGroup(CODGRUPO=item["CODGRUPO"], DESCRICAO=item["DESCRICAO"])
                    db.merge(group)
                    total_count += 1
                else:
                    logger.warning(f"Item de grupo malformado ignorado: {item}")

            db.commit()

            # Avança página; se total conhecido, usa como limite
            if total:
                total_pages = max(1, (total + limit - 1) // limit)
                if page >= total_pages:
                    break
            if len(items) < limit:
                break
            page += 1

        logger.info(f"✅ Sincronização de grupos concluída. {total_count} grupos sincronizados.")

    except Exception as e:
        logger.error(f"[ERRO GRUPOS] Falha na sincronização de grupos: {e}", exc_info=True)
        db.rollback()
        raise

# ===================== Produtos =====================

def sync_products(db: Session):
    """Sincroniza todos os produtos da TGA para o banco local, com remoção dos ausentes."""
    if not API_BASE or not API_KEY:
        logger.error("[ERRO PRODUTOS] Variáveis de ambiente da API TGA ausentes.")
        return

    # Garante execução única entre múltiplos workers
    if not acquire_sync_lock(db):
        logger.info("Outro processo já está executando a sincronização (advisory lock não adquirido). Abortando esta execução.")
        return

    logger.info("▶️ Iniciando sincronização COMPLETA de produtos...")

    try:
        # ===== Passo 1: Obter total de páginas e todos os códigos =====
        limit = 100
        page = 1
        all_codes: set[str] = set()

        # Primeira página para meta
        payload = get_tga_json_with_retry(
            f"{API_BASE}/v1/produtos", {"page": page, "limit": limit, "fields": "CODPRD"}
        )
        items, total = extract_items_and_total(payload)
        for it in items:
            cod = it.get("CODPRD")
            if cod:
                all_codes.add(cod)
        total_pages = max(1, (total + limit - 1) // limit) if total else page

        # Demais páginas
        for page in range(2, total_pages + 1):
            payload = get_tga_json_with_retry(
                f"{API_BASE}/v1/produtos", {"page": page, "limit": limit, "fields": "CODPRD"}
            )
            items, _ = extract_items_and_total(payload)
            for it in items:
                cod = it.get("CODPRD")
                if cod:
                    all_codes.add(cod)

        logger.info(f"Encontrados {len(all_codes)} códigos de produto na TGA.")

        # ===== Passo 2: Remover produtos locais que não existem mais na TGA =====
        local_codes = {p.CODPRD for p in db.query(Product.CODPRD).all()}
        to_delete = local_codes - all_codes
        if to_delete:
            logger.info(f"Removendo {len(to_delete)} produtos ausentes na TGA...")
            db.query(Product).filter(Product.CODPRD.in_(list(to_delete))).delete(synchronize_session=False)
            db.commit()

        # ===== Passo 3: Upsert (detalhes completos) =====
        group_map = {g.CODGRUPO: g.DESCRICAO for g in db.query(ProductGroup).all()}
        logger.info(f"Iniciando upsert de detalhes. {len(group_map)} grupos em cache.")

        page = 1
        payload = get_tga_json_with_retry(
            f"{API_BASE}/v1/produtos", {"page": page, "limit": limit}
        )
        items, total = extract_items_and_total(payload)
        total_pages = max(1, (total + limit - 1) // limit) if total else page

        def upsert_items(batch: List[dict]):
            for item in batch:
                cod = item.get("CODPRD")
                if not cod:
                    continue
                details = {
                    "NOMEFANTASIA": item.get("NOMEFANTASIA"),
                    "PRECO1": item.get("PRECO1", 0.0) if item.get("PRECO1") is not None else 0.0,
                    "PRECO2": item.get("PRECO2", 0.0) if item.get("PRECO2") is not None else 0.0,
                    "CODGRUPO": item.get("CODGRUPO"),
                    "group_description": group_map.get(item.get("CODGRUPO"), "")
                }
                existing = db.query(Product).filter(Product.CODPRD == cod).first()
                if existing:
                    existing.NOMEFANTASIA = details["NOMEFANTASIA"]
                    existing.PRECO1 = details["PRECO1"]
                    existing.PRECO2 = details["PRECO2"]
                    existing.CODGRUPO = details["CODGRUPO"]
                    existing.group_description = details["group_description"]
                else:
                    db.add(Product(CODPRD=cod, **details))

        if items:
            upsert_items(items)
            db.commit()

        for page in range(2, total_pages + 1):
            payload = get_tga_json_with_retry(
                f"{API_BASE}/v1/produtos", {"page": page, "limit": limit}
            )
            items, _ = extract_items_and_total(payload)
            if not items:
                continue
            upsert_items(items)
            db.commit()

        logger.info("✅ Sincronização bem-sucedida (produtos atualizados/removidos conforme TGA).")

    except Exception as e:
        logger.error(f"[ERRO PRODUTOS] Falha inesperada na sincronização: {e}", exc_info=True)
        db.rollback()
    finally:
        release_sync_lock(db)