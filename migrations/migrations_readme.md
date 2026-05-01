@'
# Database Migrations

SQL scripts for evolving the `eng_prof_test` database schema.
Apply them in chronological order against your local PostgreSQL
database before running the app for the first time, or after pulling
new code that includes a new migration.

## Running a migration

In pgAdmin:

1. Connect to your PostgreSQL server
2. Right-click the `eng_prof_test` database -> Query Tool
3. Open the `.sql` file or copy-paste its contents
4. Click Execute (the play button or F5)
5. You should see "COMMIT" or "Query returned successfully"

Each script uses `ADD COLUMN IF NOT EXISTS` so it is safe to run twice.

## Migrations in order

Run these on a fresh database in this exact order:

1. **add_access_code.sql** - adds access_code, failed_code_attempts,
   code_locked columns for the 6-digit code-gated invitation flow.
2. **add_tab_switches.sql** - adds tab_switches_count,
   tab_switches_total_seconds columns for tab-switch telemetry.

## What goes wrong if you skip a migration

The app crashes on the first query with:

    psycopg2.errors.UndefinedColumn: column invitations.<name> does not exist

The fix is always: find the migration that adds that column, run it.
'@ | Set-Content migrations\README.md