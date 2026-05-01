# Alembic — Database Schema Migrations

This doc explains what Alembic is, why we use it, and exactly how teammates set up their local database for the first time and apply future schema changes. It's the only thing you need to read to be productive on the database side of this project.

If you only have one minute, skip to **Section 4: First-time setup for a new teammate** — that's the recipe.

---

## 1. What Alembic is

Alembic is the official schema-migration tool for SQLAlchemy. Think of it as **`git` for your database structure** — every change to the schema (a new table, a new column, a renamed field) is recorded as a versioned, reviewable, reversible script that lives in your repo.

A migration is just a Python file that says "to upgrade from version X to version Y, run these SQL statements; to downgrade from Y to X, run these other statements." Each migration has a unique 12-character hash for an ID, and they form a linear chain — every migration knows which one came before it.

You'll find ours in `backend/alembic/versions/`. Right now there's one:

```
backend/alembic/versions/17b107e830d6_initial_schema.py
```

That migration creates all 10 tables (`hr_admins`, `passages`, `questions`, `speaking_topics`, `writing_topics`, `invitations`, `mcq_answers`, `audio_recordings`, `writing_responses`, `scores`). When you point Alembic at an empty database and tell it "upgrade to head," it runs that file and your DB is fully built.

---

## 2. Why we use it

Earlier in this project we used SQLAlchemy's `Base.metadata.create_all()` (called from `init_db()` in `database.py`). That's fine for the very first run — it creates whatever tables don't exist yet. But it has a serious limitation: **it cannot alter existing tables**. If you add a new column to a model, `create_all()` does not add the column to a table that already exists. The change is silently ignored.

That's why we hit a problem when we added the Writing section: we'd added `writing_topics`, `writing_responses`, plus new columns on `invitations` and `scores`. We had to write a one-off `migrate_writing.py` script with raw `ALTER TABLE` SQL. It worked, but doing that for every schema change is tedious and error-prone.

Alembic solves this. Whenever you change a model, you ask Alembic to compare the model code to the live DB and **autogenerate** a migration script that reflects the difference. You commit that script to the repo. Every teammate (and every deployment) runs `alembic upgrade head` to bring their DB in sync.

| Approach | When to use |
|---|---|
| `init_db()` / `create_all()` | First-ever run on a brand-new database. Fast for prototypes. |
| **Alembic migrations** | **Every schema change after the initial creation.** |

We still call `init_db()` on app startup — that handles brand-new DBs gracefully — but Alembic is the **source of truth** for any schema evolution.

---

## 3. How our Alembic setup is wired

Three files matter:

### `backend/alembic.ini`
Top-level config. Points at the `alembic/` folder and sets logging defaults. We deliberately do **not** put a real database connection string here — it's overridden at runtime by `env.py` so we never commit credentials.

### `backend/alembic/env.py`
Runs every time you invoke `alembic`. Its job:

1. Add `backend/` to `sys.path` so `from models import Base` works.
2. Load `.env` (same `.env` the app uses).
3. Set `sqlalchemy.url` to whatever `DATABASE_URL` resolves to in `.env`. **This means Alembic always targets the same database the app does.**
4. Set `target_metadata = Base.metadata`. This is what `--autogenerate` diffs against. Every model class in `models.py` is registered on `Base`, so Alembic can detect any schema drift.

### `backend/alembic/versions/`
The migration files themselves. Each one has:
- A unique `revision` ID
- A `down_revision` ID pointing at the previous migration (forming a chain)
- An `upgrade()` function (apply this migration)
- A `downgrade()` function (undo this migration)

Never edit a migration file after it's been pushed to a shared branch — other developers may have already applied it. If you need to fix a mistake, create a *new* migration that corrects it.

---

## 4. First-time setup for a new teammate

This is the recipe to go from a freshly cloned repo to a working local development environment. Roughly 10 minutes.

### Prerequisites
- Python 3.10+
- PostgreSQL installed and running locally (Postgres.app or Homebrew)
- pgAdmin (or any Postgres GUI) — recommended for inspecting tables visually

### Step 1: Clone and install

```bash
git clone https://github.com/eedalachaitanya-creator/English_proficiency_test.git
cd English_proficiency_test/backend
python3 -m pip install -r requirements.txt
```

The install pulls in `alembic` along with everything else.

### Step 2: Create an empty database in pgAdmin

1. Open pgAdmin and connect to your local PostgreSQL server.
2. In the left tree: right-click **Databases** → **Create** → **Database…**
3. Fill in:
   - **Database:** `ept`
   - **Owner:** your default user (e.g., `postgres` or your Mac username)
4. Click the **Definition** tab.
5. Set **Template** to `template0`. *Why this matters:* if you leave it as `template1` and your Postgres install uses an ICU locale provider, you'll get a "locale provider mismatch" error. `template0` is locale-neutral and avoids the issue.
6. Click **Save**.

You should now see `ept` in the tree under Databases. It's empty — no tables, no schemas of our making.

### Step 3: Configure `.env`

```bash
cp .env.example .env
```

Open `backend/.env` and set at minimum:

```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ept
SESSION_SECRET=<random 48-char string — generate with: python3 -c "import secrets; print(secrets.token_urlsafe(48))">
```

If your `postgres` user has no password, omit the `:YOUR_PASSWORD` part:
```
DATABASE_URL=postgresql://postgres@localhost:5432/ept
```

### Step 4: Apply all migrations

This is the magic step. Alembic looks at the chain of migrations in `alembic/versions/` and applies every one that hasn't been applied yet. On a brand-new DB, that's all of them.

```bash
cd backend
alembic upgrade head
```

You should see output like:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 17b107e830d6, initial schema
```

That single line at the bottom is Alembic saying "I created the entire schema in one go." It also writes a row to a small bookkeeping table called `alembic_version` so it knows what revision the DB is currently at.

### Step 5: Verify in pgAdmin

In pgAdmin's left tree, right-click `ept` → **Refresh**. Then expand:

```
ept → Schemas → public → Tables
```

You should see 11 entries:

```
alembic_version
audio_recordings
hr_admins
invitations
mcq_answers
passages
questions
scores
speaking_topics
writing_responses
writing_topics
```

The `alembic_version` table is Alembic's bookkeeping. Click it → View/Edit Data → All Rows. You'll see one row containing the current revision ID (e.g., `17b107e830d6`). Don't edit this manually.

### Step 6: Seed content and create your HR account

```bash
python3 seed.py                              # adds passages, questions, speaking + writing topics
python3 create_hr.py --name "Your Name" --email you@x.com --password "Pwd123"
```

### Step 7: Run the server

```bash
uvicorn main:app --reload --port 8000
```

Visit `http://localhost:8000/` — HR sign-in. You're done.

---

## 5. Day-to-day workflow: changing the schema

This is what happens **after** the initial setup, when you (or someone on the team) needs to add a column, add a table, rename a field, etc.

### Step 1: Edit `models.py`

Make the change in code first. For example, add a new column to the `Score` model:

```python
class Score(Base):
    __tablename__ = "scores"
    # ...existing columns...
    reviewed_at = Column(DateTime, nullable=True)   # ← new
```

### Step 2: Generate a migration

From inside `backend/`:

```bash
alembic revision --autogenerate -m "add reviewed_at to scores"
```

Alembic will:
1. Connect to your local DB.
2. Compare `models.py` (what code says the schema should be) to the live DB (what the schema actually is).
3. Detect the difference (new column).
4. Generate a new file in `alembic/versions/` with both `upgrade()` (add the column) and `downgrade()` (drop the column).

The new file will have a name like `b8e2ff0a3c1d_add_reviewed_at_to_scores.py`.

### Step 3: Review the generated migration

**This step is non-optional.** Autogenerate isn't perfect. It can miss some changes (renames look like drop-and-add) and it can sometimes generate something that runs but isn't quite what you wanted. Open the file and:

- Confirm the `upgrade()` function does what you intended.
- Confirm the `downgrade()` function correctly reverses it.
- For renames: replace the auto-generated `op.drop_column()` + `op.add_column()` with `op.alter_column(..., new_column_name=...)`.

### Step 4: Apply the migration locally

```bash
alembic upgrade head
```

This runs your new migration. Verify in pgAdmin that the column actually exists.

### Step 5: Commit both files

```bash
git add backend/models.py backend/alembic/versions/b8e2ff0a3c1d_add_reviewed_at_to_scores.py
git commit -m "Add reviewed_at column to scores"
git push
```

When teammates pull this branch, they'll run `alembic upgrade head` and their DB picks up the same change.

### Step 6: When teammates pull your changes

```bash
git pull
cd backend
alembic upgrade head
```

That's it. They never have to touch raw SQL or remember which `ALTER TABLE` to run. Alembic figures out which migrations have been applied (via the `alembic_version` row) and runs only the new ones.

---

## 6. Common commands cheat sheet

Run these from inside `backend/`.

| Command | What it does |
|---|---|
| `alembic upgrade head` | Apply all pending migrations to bring DB to the latest revision |
| `alembic upgrade +1` | Apply just the next pending migration |
| `alembic downgrade -1` | Roll back the most recent migration (run its `downgrade()`) |
| `alembic downgrade base` | Roll back **all** migrations — empties the schema. Use with care. |
| `alembic current` | Show which revision the DB is currently at |
| `alembic history` | List every migration in chain order |
| `alembic history --verbose` | Show full migration content, useful for review |
| `alembic revision -m "message"` | Create an empty migration file (you write the SQL yourself) |
| `alembic revision --autogenerate -m "message"` | **Most common.** Diff models.py against the DB and generate a migration |
| `alembic show <revision>` | Print one specific migration |
| `alembic check` | Verify there are no pending changes between models and the DB (CI-friendly) |

---

## 7. Common pitfalls

### "Target database is not up to date"
You ran `alembic revision --autogenerate` against a DB that's behind on migrations. Run `alembic upgrade head` first, then try again.

### Autogenerate produces an empty migration
Either there really are no changes (your `models.py` matches the DB) or `target_metadata` isn't set up correctly. Check `alembic/env.py` — `target_metadata = Base.metadata` must be after every model is imported. In our case, that's already correct.

### "Multiple heads detected"
Two developers each generated a migration on top of the same parent revision and both got merged. Run:
```bash
alembic merge -m "merge heads" <hash1> <hash2>
```
This creates a tiny merge migration that joins the chains back together.

### Autogenerate didn't pick up a column rename
Renames look like drop+add to autogenerate. Open the migration file and replace the auto-generated `op.drop_column()` and `op.add_column()` calls with a single `op.alter_column(..., new_column_name=...)` call. Also do the equivalent in `downgrade()`.

### "I made a mistake in my migration after pushing"
If teammates haven't pulled yet, you can `git push --force` after editing the file (cautious — coordinate with them first).
If anyone else has already applied the migration locally, **never edit the file**. Instead, write a follow-up migration that fixes the mistake. Migrations are append-only history.

### `alembic upgrade head` errors with "relation already exists"
Almost always means your DB has tables that Alembic doesn't know about — usually because someone created them via `init_db()` on the same database. Two options:

- If the tables match what the migration would have created, run `alembic stamp head` to tell Alembic "I'm already at the latest revision, just record that." This skips applying any migrations but updates `alembic_version`.
- If they don't match, easiest fix: drop the database in pgAdmin, recreate it empty (with `template0`), and run `alembic upgrade head` cleanly.

### Migration runs locally but fails on Render / production
Usually a dialect difference. SQLite tolerates a lot that Postgres rejects. Always test migrations against a Postgres DB before deploying. Our `env.py` reads `DATABASE_URL`, so just point it at a local Postgres instance to test.

---

## 8. Working with pgAdmin alongside Alembic

pgAdmin is great for **inspecting** the database — looking at rows, viewing column types, running ad-hoc queries. It is not how you should make schema changes on this project.

**Don't:**
- Use pgAdmin's "Create Column" or "Drop Table" UI to modify the schema. Alembic won't know about your change.
- Edit the `alembic_version` table by hand.
- Run raw `CREATE TABLE` or `ALTER TABLE` SQL except for emergencies.

**Do:**
- Use pgAdmin to **create** the empty `ept` database before running `alembic upgrade head`.
- Use pgAdmin to **inspect** rows and columns once Alembic has built the schema.
- Use pgAdmin's Query Tool for **read-only** debugging: `SELECT * FROM invitations WHERE submitted_at IS NOT NULL ORDER BY id DESC LIMIT 5;`
- Use pgAdmin to **drop and recreate** the database (with `template0`) if your local schema gets into a weird state — easier than reasoning about it.

---

## 9. Checking that everything is set up correctly

A quick sanity check anyone can run:

```bash
cd backend
alembic current             # should print: 17b107e830d6 (head)
alembic check               # should print: No new upgrade operations detected.
```

If both look right, your local schema matches what the code expects.

---

## 10. Summary in five lines

1. **`models.py` is the source of truth** for what tables and columns should exist.
2. **Alembic generates migrations** that bring an existing DB up to date with the models.
3. **Migrations live in `backend/alembic/versions/`** and form a linear, append-only history.
4. **`alembic upgrade head`** is the one command teammates run after pulling new code that touched the schema.
5. **Never edit a migration after it's been shared** — write a new one to fix mistakes.

That's the whole tool. It's small, but it saves a lot of pain once you're past the prototype phase.
