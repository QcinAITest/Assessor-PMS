import os
import urllib.parse
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Load .env file automatically
load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./qci_pms.db")


def _clean_db_url(url: str) -> str:
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            last_at_idx = rest.rfind("@")
            user_pass = rest[:last_at_idx]
            host_db = rest[last_at_idx + 1 :]
            if ":" in user_pass:
                user, password = user_pass.split(":", 1)
                password = urllib.parse.quote_plus(urllib.parse.unquote(password))
                return f"{scheme}://{user}:{password}@{host_db}"
    return url


DATABASE_URL = _clean_db_url(DATABASE_URL)

# SQLite needs check_same_thread=False; Postgres doesn't need it
connect_args = (
    {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
