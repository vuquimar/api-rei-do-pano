# backend/models.py
from sqlalchemy import Column, String, Float, Integer, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import TSVECTOR

DATABASE_URL = "postgresql://user:password@db:5432/tga_store"
engine = create_engine(DATABASE_URL)
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