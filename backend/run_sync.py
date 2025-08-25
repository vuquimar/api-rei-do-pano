import logging
from tga_client import sync_products
from database.session import SessionLocal_sync

# Configuração básica do logging para ver a saída no console do Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Iniciando script de sincronização one-off...")
    db = SessionLocal_sync()

    try:
        sync_groups(db)
        sync_products(db)
    finally:
        db.close()

    logger.info("Script de sincronização one-off concluído.")
