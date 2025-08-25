# backend/models.py
from sqlalchemy import Column, String, Float, Integer, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import TSVECTOR
import os
from dotenv import load_dotenv

load_dotenv()

def get_engine():
    """Cria e retorna uma nova engine de banco de dados lendo a DATABASE_URL."""
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        # Fallback para o ambiente de desenvolvimento local se a variável não estiver definida
        DATABASE_URL = "postgresql://user:password@db:5432/tga_store"
    return create_engine(DATABASE_URL)

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Product(Base):
    __tablename__ = "products"

    CODPRD = Column(String, primary_key=True, index=True)
    NOMEFANTASIA = Column(String, index=True)
    PRECO2 = Column(Float)
    PRECO1 = Column(Float)
    SALDOGERALFISICO = Column(Float)
    CODGRUPO = Column(String)
    CODBARRAS = Column(String)
    search_vector = Column(TSVECTOR)