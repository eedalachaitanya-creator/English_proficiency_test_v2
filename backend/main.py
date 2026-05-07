"""
FastAPI app entry point. Serves the JSON API only; the Angular SPA lives in
its own repo (https://github.com/eedalachaitanya-creator/English_proficiency_frontend)
and is deployed independently.

Run from inside the backend/ folder:
    uvicorn main:app --reload --port 8000

Then visit http://localhost:8000/api/health to confirm the server is up.
Cross-origin requests from the Angular dev server / production host are
controlled by CORS_ALLOWED_ORIGINS in .env.
"""
import os
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware


# database.py calls load_dotenv() at import time, so env vars are populated by here.
import database  # noqa: F401

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

DEV_SESSION_SECRET = "dev-only-secret-change-in-production"
DEV_APP_BASE_URL = "http://localhost:8000"
SESSION_SECRET = os.getenv("SESSION_SECRET", DEV_SESSION_SECRET)
APP_BASE_URL = os.getenv("APP_BASE_URL", DEV_APP_BASE_URL)

# Cookie Secure flag — defaults to ON in production, OFF in dev. Override with
# SESSION_COOKIE_SECURE=false when running production-mode behind plain HTTP
# (e.g., a LAN-only deployment at http://10.0.0.14 with no TLS termination).
# Without this override, browsers refuse to send the session cookie over HTTP
# and HR login appears to succeed but every follow-up request returns 401.
SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE",
    "true" if IS_PRODUCTION else "false",
).lower() == "true"

# Production must have a real session secret — refuse to start otherwise.
if IS_PRODUCTION and SESSION_SECRET == DEV_SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET environment variable must be set in production. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )

# Same guard for APP_BASE_URL — if this stays at the dev default in
# production, every invitation email goes out with http://localhost:8000/exam/...
# URLs that don't work for any candidate. Fail fast at startup instead.
if IS_PRODUCTION and APP_BASE_URL == DEV_APP_BASE_URL:
    raise RuntimeError(
        "APP_BASE_URL environment variable must be set in production. "
        "Example: APP_BASE_URL=https://app.yourcompany.com or http://10.0.0.14"
    )

# Loud warning if running on the dev secret in development. (In production we
# already raised RuntimeError above, so this branch is dev-only.)
if not IS_PRODUCTION and SESSION_SECRET == DEV_SESSION_SECRET:
    warnings.warn(
        "SESSION_SECRET is unset; using insecure dev default. "
        "Set SESSION_SECRET in .env before deploying to production.",
        stacklevel=2,
    )

if IS_PRODUCTION:
    _DEFAULT_CORS = ""  # Force CORS_ALLOWED_ORIGINS env var to be set explicitly
else:
    _DEFAULT_CORS = (
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://localhost:4200,http://127.0.0.1:4200,"
        "http://localhost:5173,http://127.0.0.1:5173"
    )

CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()
]

if IS_PRODUCTION and not CORS_ALLOWED_ORIGINS:
    raise RuntimeError(
        "CORS_ALLOWED_ORIGINS environment variable must be set in production. "
        "Example: CORS_ALLOWED_ORIGINS=https://app.yourcompany.com"
    )


# ------------------------------------------------------------------
# App lifecycle
# ------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Schema is managed by Alembic — startup just notes it's up and assumes
    `alembic upgrade head` has been run. Failing fast at first request if a
    table is missing is preferable to silently auto-creating a divergent
    schema (see docstring in database.py for the history).
    """
    print("[startup] DB ready.")
    yield
    print("[shutdown] goodbye.")


app = FastAPI(
    title="FluentiQ API",
    version="0.1.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------
# Middleware
# ------------------------------------------------------------------
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=8 * 60 * 60,           # 8-hour HR session
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)

# Cookie-credentialed APIs require an explicit origin allowlist (no "*").
# Override via CORS_ALLOWED_ORIGINS env var on Render deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Routes (mounted from separate modules — added in next batch)
# ------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Liveness check. Hit this to confirm the server is running."""
    return {"status": "ok", "service": "ept-backend"}


# Routers (defined in backend/routes/*.py)
from routes import hr as hr_routes
from routes import candidate as candidate_routes
from routes import submit as submit_routes
from routes import hr_content as hr_content_routes
from routes import admin as admin_routes
from routes import hr_reports as hr_reports_routes
from routes import admin_reports as admin_reports_routes

app.include_router(hr_routes.router)
app.include_router(candidate_routes.router)
app.include_router(submit_routes.router)
app.include_router(hr_content_routes.router)
app.include_router(admin_routes.router)
app.include_router(hr_reports_routes.router)
app.include_router(admin_reports_routes.router)



# Catch-all so an unknown /api/... path returns 404 JSON.
@app.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def api_not_found(full_path: str):
    raise HTTPException(status_code=404, detail=f"API endpoint not found: /api/{full_path}")