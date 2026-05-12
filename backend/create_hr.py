"""
CLI helper to add an HR admin user.

Usage:
    python create_hr.py --name "Chaitanya" --email cveedala@example.com \
        --password "<strong>" --organization-id 1

Run without --organization-id to see a list of available organizations.

After multi-tenancy: every HR row MUST have a non-NULL organization_id
(enforced by ck_hr_admins_role_org_consistency).

If the email already exists, the script will refuse to overwrite — use
--force to update the password (and the name) for that email instead.
--force does NOT change the user's organization or role.

Run from inside the backend/ folder so the imports resolve correctly.
"""
import argparse
import sys

from database import SessionLocal
from models import HRAdmin, Organization
from auth import hash_password


def _print_available_orgs(db) -> None:
    """List all non-soft-deleted orgs."""
    orgs = (
        db.query(Organization)
        .filter(Organization.deleted_at.is_(None))
        .order_by(Organization.id)
        .all()
    )
    if not orgs:
        print(
            "ERROR: no organizations exist in the database. Bootstrap an\n"
            "organization first via SQL, then re-run this command.",
            file=sys.stderr,
        )
        return
    print("Available organizations (pass --organization-id N):", file=sys.stderr)
    for org in orgs:
        disabled = " [DISABLED]" if org.disabled_at else ""
        print(f"  {org.id:>3}  {org.name}  (slug: {org.slug}){disabled}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Create or update an HR admin user.")
    parser.add_argument("--name", required=True, help="Display name (e.g. 'Chaitanya')")
    parser.add_argument("--email", required=True, help="Login email")
    parser.add_argument("--password", required=True, help="Plaintext password (will be hashed)")
    parser.add_argument(
        "--organization-id",
        type=int,
        required=False,
        help="Organization id to attach this HR to. Run without this flag "
             "to see the list of available orgs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="If email already exists, overwrite name + password instead of "
             "erroring out. Does NOT change role or organization.",
    )
    args = parser.parse_args()

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
        if args.organization_id is None:
            _print_available_orgs(db)
            print(
                "\nRe-run with --organization-id <N>.",
                file=sys.stderr,
            )
            sys.exit(2)

        org = (
            db.query(Organization)
            .filter(
                Organization.id == args.organization_id,
                Organization.deleted_at.is_(None),
            )
            .first()
        )
        if org is None:
            print(
                f"Error: organization id={args.organization_id} not found "
                f"(or is soft-deleted).",
                file=sys.stderr,
            )
            _print_available_orgs(db)
            sys.exit(1)
        if org.disabled_at is not None:
            print(
                f"Warning: organization '{org.name}' is currently disabled. "
                f"This HR's logins will be rejected until the org is re-enabled.",
                file=sys.stderr,
            )

        existing = db.query(HRAdmin).filter(HRAdmin.email == email).first()

        # Cross-role refusal: do not silently demote admins or supers to HR.
        if existing and existing.role != "hr":
            print(
                f"Error: a {existing.role!r} account with email '{email}' "
                f"already exists (id={existing.id}).\n"
                f"Refusing to silently change role. Delete or rename the "
                f"existing account first.",
                file=sys.stderr,
            )
            sys.exit(1)

        if existing and not args.force:
            print(
                f"Error: HR admin with email '{email}' already exists (id={existing.id}).\n"
                f"Pass --force to overwrite their name and password.",
                file=sys.stderr,
            )
            sys.exit(1)

        if existing:
            # --force path: only name and password change. Org stays as-is.
            existing.name = name
            existing.password_hash = hash_password(args.password)
            db.commit()
            print(
                f"Updated HR admin (id={existing.id}, email={email}, "
                f"organization_id={existing.organization_id}). "
                f"Organization was NOT changed."
            )
        else:
            hr = HRAdmin(
                name=name,
                email=email,
                password_hash=hash_password(args.password),
                role="hr",
                organization_id=args.organization_id,
            )
            db.add(hr)
            db.commit()
            db.refresh(hr)
            print(
                f"Created HR admin (id={hr.id}, email={email}, "
                f"organization_id={hr.organization_id}, "
                f"organization='{org.name}')."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()