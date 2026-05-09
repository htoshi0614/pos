"""db_shared.py — DB engine/session/Base/権限の共有定義"""
from typing import List, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from fastapi import HTTPException

# .env ファイルを自動読み込み（なければ無視）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = "sqlite:///./pos.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def require_role(role_header: Optional[str], allowed: List[str]):
    if not role_header:
        raise HTTPException(401, "Missing X-Role")
    if role_header not in allowed:
        raise HTTPException(403, f"Role '{role_header}' not allowed")
