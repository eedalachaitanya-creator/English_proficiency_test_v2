"""
CLI helper to create or update an admin account.

Admin accounts can ONLY be created from this script — there is no in-product
"create admin" flow. This is deliberate: admin accounts can mint HR accounts,
so creating an admin requires server access (i.e., physical/SSH access to
this machine).

Usage:
    python create_admin.py --name "Alice" --email alice@stixis.com \
        --password "<strong>" --organization-id 1

Run without --organization-id to see a list of available organizations.

After multi-tenancy: every admin row MUST have a non-NULL organization_id
(enforced by ck_hr_admins_role_org_consistency). Super accounts use
organization_id=NULL and CANNOT be created from this script — supers are
bootstrapped via a one-time SQL insert at install time.

If the email already exists with role='admin', --force will overwrite name
and password (but NOT the organization — an admin moving between orgs is
a destructive action that should go through the API). If it exists with
role='hr' or 'super', the script REFUSES — silently elevating/demoting
across roles would change a security boundary, so it must be done
explicitly via the API or via direct SQL.

See docs/superpowers/specs/2026-05-04-admin-portal-design.md.
Run from inside the backend/ folder so imports resolve.
"""
import argparse
import sys

from database import SessionLocal
from models import HRAdmin, Organization
from auth import hash_password


def _print_available_orgs(db) -> None:
    """List all active orgs so the operator can pick one. Excludes
    soft-deleted and disabled orgs because creating an admin in a
    dead org is almost certainly a mistake."""
    orgs = (
        db.query(Organization)
        .filter(Organization.deleted_at.is_(None))
        .order_by(Organization.id)
        .all()
    )
    if not orgs:
        print(
            "ERROR: no organizations exist in the database. Bootstrap an\n"
            "organization first (e.g., insert a row into `organizations`\n"
            "via SQL), then re-run this command.",
            file=sys.stderr,
        )
        return
    print("Available organizations (pass --organization-id N):", file=sys.stderr)
    for org in orgs:
        disabled = " [DISABLED]" if org.disabled_at else ""
        print(f"  {org.id:>3}  {org.name}  (slug: {org.slug}){disabled}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Create or update an admin account.")
    parser.add_argument("--name", required=True, help="Display name (e.g. 'Alice')")
    parser.add_argument("--email", required=True, help="Login email")
    parser.add_argument("--password", required=True, help="Plaintext password (will be hashed)")
    parser.add_argument(
        "--organization-id",
        type=int,
        required=False,
        help="Organization id to attach this admin to. Run without this "
             "flag to see the list of available orgs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="If an admin with this email exists, overwrite name + password.",
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
        # If org id wasn't passed, print the menu and exit cleanly. The
        # script could prompt interactively, but argparse + non-interactive
        # CI usage is cleaner with a hard requirement.
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
            # Allow but warn — there are legitimate reasons to put an admin
            # into a disabled org (re-enabling it later, audit purposes).
            print(
                f"Warning: organization '{org.name}' is currently disabled. "
                f"This admin's logins will be rejected until the org is re-enabled.",
                file=sys.stderr,
            )

        existing = db.query(HRAdmin).filter(HRAdmin.email == email).first()

        # Cross-role refusal: don't silently change a user's role via this script.
        if existing and existing.role != "admin":
            print(
                f"Error: a {existing.role!r} account with email '{email}' "
                f"already exists (id={existing.id}).\n"
                f"Refusing to silently change role. Delete or rename the "
                f"existing account first, then re-run.",
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
            # --force path: overwrite name + password, NOT organization.
            # Moving an admin between orgs is a destructive operation that
            # would orphan the org being left if it was the last admin.
            # That decision belongs in the API (Step D), not in this CLI.
            existing.name = name
            existing.password_hash = hash_password(args.password)
            db.commit()
            print(
                f"Updated admin (id={existing.id}, email={email}, "
                f"organization_id={existing.organization_id}). "
                f"Organization was NOT changed."
            )
        else:
            admin = HRAdmin(
                name=name,
                email=email,
                password_hash=hash_password(args.password),
                role="admin",
                organization_id=args.organization_id,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            print(
                f"Created admin (id={admin.id}, email={email}, "
                f"organization_id={admin.organization_id}, "
                f"organization='{org.name}')."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()