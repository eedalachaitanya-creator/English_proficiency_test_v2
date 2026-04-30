"""
FastAPI app entry point.

Run from inside the backend/ folder:
    uvicorn main:app --reload --port 8000

Then visit http://localhost:8000/api/health to confirm the server is up.
The frontend (../frontend) is served at the root URL.
"""
import os
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# database.py calls load_dotenv() at import time, so env vars are populated by here.
from database import init_db

DEV_SESSION_SECRET = "dev-only-secret-change-in-production"
SESSION_SECRET = os.getenv("SESSION_SECRET", DEV_SESSION_SECRET)
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Loud warning if running on the dev secret. Sessions are forgeable without a real secret.
if SESSION_SECRET == DEV_SESSION_SECRET:
    warnings.warn(
        "SESSION_SECRET is unset; using insecure dev default. "
        "Set SESSION_SECRET in .env before deploying.",
        stacklevel=2,
    )

# CORS: comma-separated list of allowed origins. Defaults cover same-origin dev + Vite.
_DEFAULT_CORS = (
    "http://localhost:8000,http://127.0.0.1:8000,"
    "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()
]


# ------------------------------------------------------------------
# App lifecycle
# ------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run once on startup. Creates DB tables if missing."""
    init_db()
    print(f"[startup] DB ready. Frontend served from: {FRONTEND_DIR}")
    yield
    print("[shutdown] goodbye.")


app = FastAPI(
    title="English Proficiency Test API",
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
    https_only=False,              # set True in production over HTTPS
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
app.include_router(hr_routes.router)
app.include_router(candidate_routes.router)
app.include_router(submit_routes.router)


# Catch-all so an unknown /api/... path returns 404 JSON instead of falling through
# to the SPA static mount and silently returning index.html. Must come AFTER all routers.
@app.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def api_not_found(full_path: str):
    raise HTTPException(status_code=404, detail=f"API endpoint not found: /api/{full_path}")


# ------------------------------------------------------------------
# Static frontend (mounted last so API routes take precedence)
# ------------------------------------------------------------------
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    print(f"[warn] frontend dir not found at {FRONTEND_DIR}; static mount skipped")
