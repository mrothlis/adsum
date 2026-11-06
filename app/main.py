"""FastAPI app factory and middleware."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _localtime(value, fmt: str = "%H:%M"):
    """Render a naive-UTC datetime in the institution's local timezone.

    Every timestamp in the DB is stored as naive UTC (datetime.utcnow()).
    Displaying it raw showed class times several hours off, so templates
    must use this filter instead of calling .strftime() directly.
    """
    if value is None:
        return "\u2014"
    local = _to_local(value)
    # "%-d"/"%-I" (strip leading zero) is glibc-only and raises on Windows.
    # Format with the portable code, then trim the zero ourselves.
    strip = []
    portable = fmt
    for code in ("d", "I", "m", "H", "j"):
        if f"%-{code}" in portable:
            portable = portable.replace(f"%-{code}", f"%{code}")
            strip.append(code)
    out = local.strftime(portable)
    for code in strip:
        padded = local.strftime(f"%{code}")
        if padded.startswith("0") and len(padded) > 1:
            out = out.replace(padded, padded.lstrip("0"), 1)
    return out


def _to_local(value):
    """Convert a naive-UTC datetime to DISPLAY_TIMEZONE.

    Falls back to a fixed UTC offset if the IANA tz database is unavailable
    (Windows has no system tz data unless the `tzdata` package is installed),
    so times are never silently rendered as UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return value.astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE))
    except Exception:
        return value.astimezone(dt.timezone(dt.timedelta(hours=settings.DISPLAY_UTC_OFFSET_HOURS)))


templates.env.filters["localtime"] = _localtime

# Installation-wide defaults templates may need. Exposed here so no template
# hard-codes a location belonging to one particular campus.
templates.env.globals["DEFAULT_GEOFENCE_LAT"] = settings.DEFAULT_GEOFENCE_LAT
templates.env.globals["DEFAULT_GEOFENCE_LON"] = settings.DEFAULT_GEOFENCE_LON
templates.env.globals["DEFAULT_GEOFENCE_RADIUS_M"] = settings.DEFAULT_GEOFENCE_RADIUS_M
templates.env.globals["INSTITUTION_NAME"] = settings.INSTITUTION_NAME

app = FastAPI(title="Adsum", version="0.1.0")

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
# Imported here, not at the top: the route modules import `templates` and
# `app` from this module, so importing them any earlier is a circular import.
from app.routes.api import router as api_router  # noqa: E402
from app.routes.instructor import router as instructor_router  # noqa: E402
from app.routes.lti import router as lti_router  # noqa: E402
from app.routes.student import router as student_router  # noqa: E402

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
