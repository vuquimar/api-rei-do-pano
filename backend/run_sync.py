import logging
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from models import get_engine
from tga_client import sync_products, sync_groups

# Configuração básica do logging para ver a saída no console do Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Iniciando script de sincronização one-off...")
    
    engine = get_engine()
    SessionLocal_sync = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal_sync()
    
    try:
        # Sincroniza grupos e produtos
        sync_groups(db)
        sync_products(db)
        
        # Força a atualização do search_vector para todos os produtos
        logger.info("Forçando atualização do search_vector para todos os produtos...")
        update_query = text("""
            UPDATE products 
            SET search_vector = to_tsvector('portuguese', 
                public.immutable_unaccent(coalesce("NOMEFANTASIA", '')) || ' ' || 
                public.immutable_unaccent(coalesce(group_description, ''))
            )
        """)
        db.execute(update_query)
        db.commit()
        logger.info("Atualização do search_vector concluída com sucesso!")
        
    finally:
        db.close()

    logger.info("Script de sincronização one-off concluído.")
