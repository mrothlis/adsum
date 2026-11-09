"""The Jinja environment, separated from the application object.

This module exists to break an import cycle, and the cycle was not
theoretical: `app/main.py` creates the FastAPI app and then imports the route
modules, while every route module needs `templates`, which lived in
`app/main.py`. That only worked because `templates` happened to be defined
above the route imports, so `import app.main` succeeded by luck of ordering.

Importing a route module FIRST failed outright:

    >>> import app.routes.lti
    ImportError: cannot import name 'router' from partially initialized
    module 'app.routes.lti' (most likely due to a circular import)

which is a real constraint on anything that wants to import one route module
for a test or a script, and a trap for whoever next adds an import to a route
file. Nothing here imports `app.main`, so the cycle is gone rather than
merely dormant.

`app/main.py` re-exports `templates` and `_to_local` so existing references
keep working.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
        return value.astimezone(
            dt.timezone(dt.timedelta(hours=settings.DISPLAY_UTC_OFFSET_HOURS)))


def _localtime(value, fmt: str = "%H:%M"):
    """Render a naive-UTC datetime in the institution's local timezone.

    Every timestamp in the DB is stored as naive UTC (see app/clock.py).
    Displaying it raw showed class times several hours off, so templates
    must use this filter instead of calling .strftime() directly.
    """
    if value is None:
        # Not a dash: a screen reader announces an em dash literally, and the
        # project's rules exclude them from anything a person reads.
        return "Not recorded"
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


templates.env.filters["localtime"] = _localtime

# Installation-wide defaults templates may need. Exposed here so no template
# hard-codes a location belonging to one particular campus.
templates.env.globals["DEFAULT_GEOFENCE_LAT"] = settings.DEFAULT_GEOFENCE_LAT
templates.env.globals["DEFAULT_GEOFENCE_LON"] = settings.DEFAULT_GEOFENCE_LON
templates.env.globals["DEFAULT_GEOFENCE_RADIUS_M"] = settings.DEFAULT_GEOFENCE_RADIUS_M
templates.env.globals["INSTITUTION_NAME"] = settings.INSTITUTION_NAME
