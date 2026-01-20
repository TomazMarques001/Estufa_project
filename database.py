# database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./data.db"  # arquivo local

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # necessário para SQLite + FastAPI
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
