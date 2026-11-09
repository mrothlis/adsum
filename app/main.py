"""FastAPI app factory and middleware."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db

# templates and the timezone helpers live in app/templating.py so that route
# modules can import them without importing this file, which used to form a
# cycle: this module imports the routers, and every router needed `templates`
# from here. Re-exported for the existing call sites and for scripts.
from app.templating import (  # noqa: E402
    BASE_DIR,
    templates,
)

# There was no logging configuration at all, which meant a rejected check-in
# left no trace anywhere: if a student said "it did not work", nobody could
# find out what happened. Every refusal that a student or instructor could
# notice is now logged at WARNING with the reason, and never with a token,
# a password or a secret in it.
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
log = logging.getLogger("adsum")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup work, as a lifespan handler rather than @app.on_event.

    `on_event` is deprecated in FastAPI and warns on every boot. It also has
    no shutdown-ordering guarantee, which matters here because startup
    migrates the schema: if that ever needs to be undone or a connection
    closed in order, a context manager can express it and a pair of event
    callbacks cannot.

    The imports inside stay lazy on purpose. app.migrate and app.seed both
    import app.models, and app.routes imports this module, so importing them
    at the top of this file closes a cycle.
    """
    # Before init_db: create_all() only adds MISSING TABLES, so it silently
    # does nothing about a migration that adds a column to an existing one --
    # which is most of them. Migrating first also stops create_all racing a
    # migration that creates a table.
    from app.migrate import apply_pending
    apply_pending()
    await init_db()
    from app.seed import ensure_admin, seed_demo_data
    if settings.SEED_DEMO:
        await seed_demo_data()   # demo institution/course/students (dev only)
    else:
        await ensure_admin()     # production: create admin from .env on an empty DB

    yield
    # Nothing to tear down: the engine's pool is disposed by the event loop
    # shutdown, and a half-written request is better abandoned than finished.


# VERSION is the one place the number lives. It said 0.1.0 while the repository
# was tagged v1.0.0 and the README described a released tool, so the OpenAPI
# document and the git history disagreed about what this was.
VERSION = "1.0.1"

app = FastAPI(title="Adsum", version=VERSION, lifespan=lifespan)

# Requests that change state and are authenticated only by a cookie need a
# second line of defence behind SameSite=Lax, because SameSite is a browser
# behaviour and not a server guarantee: an older browser, or one with a
# policy override, would still attach the cookie to a cross-site POST.
_CSRF_EXEMPT_PATHS = frozenset({
    # Authenticated by its own OAuth 1.0a signature and legitimately posted
    # cross-site by D2L, so neither a cookie nor an origin check applies.
    "/lti/launch",
})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _same_origin(request: Request) -> bool:
    """Is this request from our own origin?

    Prefers Sec-Fetch-Site, which the browser sets and script cannot forge.
    Falls back to comparing the Origin header against BASE_URL for browsers
    that do not send it.
    """
    site = request.headers.get("sec-fetch-site")
    if site:
        return site in ("same-origin", "same-site", "none")

    origin = request.headers.get("origin")
    if origin:
        expected = settings.BASE_URL.rstrip("/").lower()
        if origin.rstrip("/").lower() == expected:
            return True
        # Behind a proxy BASE_URL may not match the host the browser used, so
        # accept the request's own host as well.
        host = request.headers.get("host", "")
        return origin.rstrip("/").lower().endswith("//" + host.lower())

    # No Origin and no Sec-Fetch-Site. Same-origin form posts from older
    # browsers look like this, so requiring a header outright would break
    # them; a cross-site POST, however, always carries an Origin. Accept, and
    # rely on SameSite=Lax for this shrinking case.
    return True


# Instructor pages load Leaflet (map picker) and Chart.js from these CDNs.
# Student pages load nothing third-party, which is a deliberate rule.
_CDN_SCRIPT = "https://unpkg.com https://cdn.jsdelivr.net"
_CDN_STYLE = "https://unpkg.com https://cdn.jsdelivr.net"
_TILES = "https://tile.openstreetmap.org https://*.tile.openstreetmap.org"

# 'unsafe-inline' is still needed for scripts because several templates carry
# inline <script> blocks, so this policy does not stop script injection on its
# own. It does something more useful than nothing, though: connect-src and
# form-action are restricted to this origin, so injected script cannot post
# or fetch stolen data (the LTI secret, a roster) to an attacker's server, and
# frame-ancestors stops the app being framed for clickjacking.
_CSP = (
    "default-src 'self'; "
    f"script-src 'self' 'unsafe-inline' {_CDN_SCRIPT}; "
    f"style-src 'self' 'unsafe-inline' {_CDN_STYLE}; "
    f"img-src 'self' data: {_TILES}; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.middleware("http")
async def block_cross_site_writes(request: Request, call_next):
    if (request.method in _UNSAFE_METHODS
            and request.url.path not in _CSRF_EXEMPT_PATHS
            and not _same_origin(request)):
        log.warning("Blocked cross-site %s to %s from origin %r",
                    request.method, request.url.path,
                    request.headers.get("origin"))
        return PlainTextResponse(
            "This request did not come from Adsum, so it was refused. "
            "If you reached this page from a link someone sent you, close it.",
            status_code=403,
        )
    return await call_next(request)

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
async def healthz():
    """Uptime probe for monitoring.

    Touches the database, because answering 200 just for being alive was
    actively misleading: with a missing column or a corrupt file every page
    returned 500 while this endpoint, the restart loop and any external
    monitor all reported a healthy service.
    """
    from app.database import db_healthy
    if not await db_healthy():
        log.error("Health check failed: database unreachable")
        return JSONResponse({"status": "error", "database": "unreachable"},
                            status_code=503)
    return {"status": "ok"}


# ---- Register route modules ----
# Imported here, not at the top: the route modules import `templates` and
# `app` from this module, so importing them any earlier is a circular import.
from app.routes.admin import router as admin_router  # noqa: E402
from app.routes.api import router as api_router  # noqa: E402
from app.routes.instructor import router as instructor_router  # noqa: E402
from app.routes.lti import router as lti_router  # noqa: E402
from app.routes.student import router as student_router  # noqa: E402

app.include_router(instructor_router)
app.include_router(admin_router)
app.include_router(student_router)
app.include_router(api_router, prefix="/api")
app.include_router(lti_router, prefix="/lti")


@app.get("/c")
async def short_code_entry():
    """A deliberately tiny alias for the typed check-in fallback.

    A student whose camera will not focus has to type this address off a
    projector, so every character counts: "<host>/c" rather than
    "<host>/student/code". The projected page shows this form.
    """
    return RedirectResponse("/student/code", status_code=307)


@app.get("/")
async def index(request: Request):
    from app.auth import get_current_user
    user = get_current_user(request)
    if user and user.get("role") == "instructor":
        return RedirectResponse("/instructor/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html")
