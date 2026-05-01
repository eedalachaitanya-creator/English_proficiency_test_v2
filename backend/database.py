"""
Database connection setup.

- Reads DATABASE_URL from .env (defaults to SQLite for local dev).
- Exposes `engine`, `SessionLocal`, and `Base`.
- `get_db()` is a FastAPI dependency: each request gets its own DB session
  that auto-closes when the request finishes.
- `init_db()` creates all tables. Called once at app startup.
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


def init_db():
    """
    Create all tables registered on Base.metadata. Idempotent.

    NOTE: This is a fresh-DB convenience for local dev. It does NOT alter
    existing tables, so it can't apply schema changes after the first run.
    For all schema changes, use Alembic:
        alembic revision --autogenerate -m "describe change"
        alembic upgrade head
    The app calls this on startup so a brand-new SQLite/Postgres DB still
    boots without manually running Alembic, but Alembic is the source of
    truth for any schema change after the initial creation.
    """
    # Import models so SQLAlchemy registers them on Base before create_all.
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
