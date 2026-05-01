"""
Alembic environment.

Reads DATABASE_URL from .env (same source the app uses) so migrations always
target the same DB the app will connect to. target_metadata points at our
SQLAlchemy Base, which lets `alembic revision --autogenerate` diff models.py
against the live DB and produce migration scripts automatically.
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from dotenv import load_dotenv


# ---- Make backend/ importable so `from models import Base` works ----
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load .env from backend/, the same way database.py does.
load_dotenv(BACKEND_DIR / ".env")

# Import after sys.path is set up.
from models import Base  # noqa: E402

# Alembic Config object — provides access to the values within alembic.ini.
config = context.config

# Override the alembic.ini sqlalchemy.url with the value from .env so we never
# have to commit a real connection string. If DATABASE_URL is unset, fall back
# to the local SQLite default the app uses.
db_url = os.getenv("DATABASE_URL", "sqlite:///./ept.db")
config.set_main_option("sqlalchemy.url", db_url)

# Set up Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what `--autogenerate` diffs against. Every model declared in
# models.py is registered on Base.metadata, so this picks them all up.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate raw SQL without connecting (useful for review / CI)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the live DB and apply migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Required for SQLite to support batch ALTER TABLE.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
