# backend/tga_client.py
import httpx
import os
from models import Product, SessionLocal
from dotenv import load_dotenv
from datetime import datetime, timezone
import json

load_dotenv()

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

async def sync_products_from_tga():
    """Sincroniza produtos com a API TGA"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        page = 1
        limit = 100
        db = SessionLocal()
        updated_count = 0

        last_sync = get_last_sync()
        params = {"page": page, "limit": limit}
        if last_sync:
            params["updated_after"] = last_sync

        while True:
            try:
                response = await client.get(f"{API_BASE}/v1/produtos", headers=HEADERS, params=params)
                if response.status_code != 200:
                    break
                data = response.json().get("data", [])
                if not data:
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
                    updated_count += 1

                db.commit()
                if len(data) < limit:
                    break
                page += 1
            except Exception as e:
                print(f"[ERRO] Falha na sincronização: {e}")
                break

        db.close()
        save_last_sync()
        print(f"🔄 Sincronização concluída. {updated_count} produtos atualizados.")