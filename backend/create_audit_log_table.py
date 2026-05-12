"""
One-off script: create the audit_log table in the live v2 DB.

Run from backend/:
    python3 create_audit_log_table.py

Idempotent — uses `checkfirst=True` so re-running does nothing if the
table already exists. Once a proper Alembic migration is generated to
reconcile the multi-tenancy schema, this script can be deleted.

This script intentionally only touches AuditLog.__table__ instead of
calling Base.metadata.create_all(...) wholesale, which would also
attempt to create organizations/hr_admins/etc. — those already exist
in the live DB with subtly different shapes from the model (no
Alembic migration covers them), and asking SQLAlchemy to "create if
missing" might emit warnings or compete with existing structures.
"""
from database import engine
from models import AuditLog, OrganizationContentDisable


def main() -> None:
    AuditLog.__table__.create(bind=engine, checkfirst=True)
    OrganizationContentDisable.__table__.create(bind=engine, checkfirst=True)
    print(
        "audit_log and organization_content_disable tables ready in DB =",
        engine.url.database,
    )


if __name__ == "__main__":
    main()
