"""
CLI helper to add an HR admin user.

Usage:
    python create_hr.py --name "Chaitanya" --email cveedala2002@gmail.com --password "chaitanya@123"

If the email already exists, the script will refuse to overwrite — use
--force to update the password (and the name) for that email instead.

Run from inside the backend/ folder so the imports resolve correctly.
"""
import argparse
import sys

from database import SessionLocal, init_db
from models import HRAdmin
from auth import hash_password


def main():
    parser = argparse.ArgumentParser(description="Create or update an HR admin user.")
    parser.add_argument("--name", required=True, help="Display name (e.g. 'Chaitanya')")
    parser.add_argument("--email", required=True, help="Login email")
    parser.add_argument("--password", required=True, help="Plaintext password (will be hashed)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="If email already exists, overwrite name + password instead of erroring out.",
    )
    args = parser.parse_args()

    # Ensure tables exist (idempotent)
    init_db()

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

        if existing and not args.force:
            print(
                f"Error: HR admin with email '{email}' already exists (id={existing.id}).\n"
                f"Pass --force to overwrite their name and password.",
                file=sys.stderr,
            )
            sys.exit(1)

        if existing:
            existing.name = name
            existing.password_hash = hash_password(args.password)
            db.commit()
            print(f"Updated HR admin (id={existing.id}, email={email}).")
        else:
            hr = HRAdmin(
                name=name,
                email=email,
                password_hash=hash_password(args.password),
            )
            db.add(hr)
            db.commit()
            db.refresh(hr)
            print(f"Created HR admin (id={hr.id}, email={email}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
