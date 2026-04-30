"""
One-time migration: add the Writing section's tables and columns.

Run from inside backend/:
    python3 migrate_writing.py

POSTGRES ONLY. Uses Postgres-specific SQL (SERIAL, NOW(), ALTER TABLE ADD
COLUMN IF NOT EXISTS) for in-place migration of an existing database.

If you're on the default SQLite dev setup, you don't need this script —
just delete backend/ept.db and run `python3 seed.py`. init_db() will
create all the new writing tables and columns from models.py.

Idempotent on Postgres — safe to run twice.
After this, run `python3 seed.py --reset` to load the new writing prompts.
"""
import sys

from sqlalchemy import text

from database import engine, init_db


# Raw SQL — kept here instead of relying on init_db() because Base.metadata.create_all()
# doesn't ALTER existing tables to add columns.
MIGRATIONS = [
    # New tables
    """
    CREATE TABLE IF NOT EXISTS writing_topics (
        id SERIAL PRIMARY KEY,
        prompt_text TEXT NOT NULL,
        difficulty VARCHAR(20) NOT NULL,
        min_words INTEGER NOT NULL DEFAULT 200,
        max_words INTEGER NOT NULL DEFAULT 300,
        category VARCHAR(100),
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_writing_topics_difficulty ON writing_topics(difficulty)",

    """
    CREATE TABLE IF NOT EXISTS writing_responses (
        id SERIAL PRIMARY KEY,
        invitation_id INTEGER NOT NULL UNIQUE REFERENCES invitations(id) ON DELETE CASCADE,
        topic_id INTEGER NOT NULL REFERENCES writing_topics(id),
        essay_text TEXT NOT NULL,
        word_count INTEGER NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_writing_responses_invitation_id ON writing_responses(invitation_id)",

    # New columns on existing tables (Postgres supports IF NOT EXISTS on ADD COLUMN since v9.6)
    "ALTER TABLE invitations ADD COLUMN IF NOT EXISTS assigned_writing_topic_id INTEGER REFERENCES writing_topics(id)",
    "ALTER TABLE scores ADD COLUMN IF NOT EXISTS writing_breakdown JSON",
    "ALTER TABLE scores ADD COLUMN IF NOT EXISTS writing_score INTEGER",
]


def main():
    # SQLite users don't need this script — init_db() creates everything from models.py.
    # The Postgres-specific SQL below would error out on SQLite anyway, so bail early
    # with a useful message.
    if engine.dialect.name == "sqlite":
        print(
            "This migration is Postgres-only. On SQLite, just delete backend/ept.db\n"
            "and run `python3 seed.py` — init_db() will create the writing tables\n"
            "and columns from models.py for you.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Make sure base tables already exist (so the foreign key targets are valid).
    init_db()

    with engine.begin() as conn:
        for sql in MIGRATIONS:
            print(f"-> running: {sql.strip().splitlines()[0][:90]}")
            conn.execute(text(sql))

    print()
    print("Migration complete. Now run:  python3 seed.py --reset")


if __name__ == "__main__":
    main()
