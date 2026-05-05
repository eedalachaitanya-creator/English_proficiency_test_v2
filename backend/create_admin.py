"""
CLI helper to create or update an admin account.

Admin accounts can ONLY be created from this script — there is no in-product
"create admin" flow. This is deliberate: admin accounts can mint HR accounts,
so creating an admin requires server access (i.e., physical/SSH access to
this machine).

Usage:
    python create_admin.py --name "Alice" --email alice@stixis.com --password "<strong>"

If the email already exists with role='admin', --force will overwrite name
and password. If it exists with role='hr', the script REFUSES — silently
elevating an HR to admin would change a security boundary, so an admin
must explicitly delete the HR account first (or rename the email).

See docs/superpowers/specs/2026-05-04-admin-portal-design.md.
Run from inside the backend/ folder so imports resolve.
"""
import argparse
import sys

from database import SessionLocal
from models import HRAdmin
from auth import hash_password


def main():
    parser = argparse.ArgumentParser(description="Create or update an admin account.")
    parser.add_argument("--name", required=True, help="Display name (e.g. 'Alice')")
    parser.add_argument("--email", required=True, help="Login email")
    parser.add_argument("--password", required=True, help="Plaintext password (will be hashed)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="If an admin with this email exists, overwrite name + password.",
    )
    args = parser.parse_args()

    # Schema is managed by alembic — assume `alembic upgrade head` has been run.
    email = args.email.strip().lower()
    name = args.name.strip()

    if not email or "@" not in email:
        print(f"Error: invalid email '{args.email}'", file=sys.stderr)
        sys.exit(1)
    if len(args.password) < 6:
        print("Error: password must be at least 6 characters.", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.query(HRAdmin).filter(HRAdmin.email == email).first()

        if existing and existing.role == "hr":
            print(
                f"Error: an HR account with email '{email}' already exists (id={existing.id}).\n"
                f"Refusing to silently elevate an HR to admin. Delete or rename the\n"
                f"HR account first, then re-run this command.",
                file=sys.stderr,
            )
            sys.exit(1)

        if existing and existing.role == "admin" and not args.force:
            print(
                f"Error: admin with email '{email}' already exists (id={existing.id}).\n"
                f"Pass --force to overwrite their name and password.",
                file=sys.stderr,
            )
            sys.exit(1)

        if existing:
            existing.name = name
            existing.password_hash = hash_password(args.password)
            db.commit()
            print(f"Updated admin (id={existing.id}, email={email}).")
        else:
            admin = HRAdmin(
                name=name,
                email=email,
                password_hash=hash_password(args.password),
                role="admin",
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            print(f"Created admin (id={admin.id}, email={email}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
