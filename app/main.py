"""FastAPI app factory and middleware."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="CSU Check-In", version="0.1.0")

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    from app.seed import ensure_admin, seed_demo_data
    if settings.SEED_DEMO:
        await seed_demo_data()   # demo institution/course/students (dev only)
    else:
        await ensure_admin()     # production: create admin from .env on an empty DB


@app.get("/healthz")
async def healthz() -> dict:
    """Uptime probe for monitoring."""
    return {"status": "ok"}


# ---- Register route modules ----
from app.routes.instructor import router as instructor_router
from app.routes.student import router as student_router
from app.routes.api import router as api_router
from app.routes.lti import router as lti_router

app.include_router(instructor_router)
app.include_router(student_router)
app.include_router(api_router, prefix="/api")
app.include_router(lti_router, prefix="/lti")


@app.get("/")
async def index(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    if user and user.get("role") == "instructor":
        return RedirectResponse("/instructor/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html")
