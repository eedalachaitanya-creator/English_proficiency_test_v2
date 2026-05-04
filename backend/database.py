"""
Database connection setup.

- Reads DATABASE_URL from .env (defaults to SQLite for local dev).
- Exposes `engine`, `SessionLocal`, and `Base`.
- `get_db()` is a FastAPI dependency: each request gets its own DB session
  that auto-closes when the request finishes.

Schema management is handled exclusively by Alembic — there is no
create_all() at startup. Run `alembic upgrade head` after pulling new
migrations. (Background: create_all silently creates new TABLES from model
definitions but never adds new COLUMNS to existing tables, which made the
schema diverge from alembic's view and broke later migrations.)
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ept.db")

# SQLite needs this flag because FastAPI uses multiple threads per request lifecycle.
# It's a no-op for Postgres.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency. Yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
