import logging
import sys
from models import SessionLocal
from tga_client import sync_products, sync_groups

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Iniciando script de pré-inicialização: Sincronização de dados.")
    db = SessionLocal()
    try:
        sync_groups(db)
        sync_products(db)
        logger.info("Sincronização de dados de pré-inicialização concluída com sucesso.")
    except Exception as e:
        logger.error(f"Ocorreu um erro durante a sincronização de pré-inicialização: {e}", exc_info=True)
        # Falha o processo de deploy se a sincronização inicial não puder ser concluída
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
