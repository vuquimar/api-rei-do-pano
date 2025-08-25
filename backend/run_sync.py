import logging
from tga_client import sync_products

# Configuração básica do logging para ver a saída no console do Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Iniciando script de sincronização one-off...")
    sync_products()
    logger.info("Script de sincronização one-off concluído.")
