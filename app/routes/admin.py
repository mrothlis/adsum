"""Installation administration: accounts and the shared D2L connection.

Deliberately NOT a window onto everything. An admin here manages who has an
account and how the install talks to D2L; an admin cannot read any course's
attendance. That boundary is the whole point of the page:

  * Attendance records are student education records (FERPA). The isolation
    suite guarantees instructor A cannot reach instructor B's students, and
    that guarantee is worth nothing if it quietly ends at "unless they are an
    admin". Someone keeping the server running does not need to read
    attendance to do it.
  * So there is no cross-course browsing, no global attendance report and no
    impersonation here, and there should never be one. If an admin needs
    attendance for a course, they should be added to that course as an
    instructor, visibly.

Things that belong to the deployment rather than the institution (SECRET_KEY,
DATABASE_URL, DEBUG) stay in .env and are shown here read-only. Editing them
from a web form is how an app ends up unable to boot.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, hash_password
from app.authz import require_admin
from app.config import settings
from app.database import get_db
from app.main import templates
from app.models import Course, CourseEnrollment, Institution, Instructor

router = APIRouter(prefix="/admin", tags=["admin"])

FORBIDDEN = HTMLResponse(
    "<h1>403 Forbidden</h1><p>This page is for installation administrators.</p>",
    status_code=403,
)


def _install_health() -> list[dict]:
    """Read-only checks a self-hoster actually needs after installing.

    Everything here is derived from configuration already loaded, so the page
    cannot be used to probe the filesystem.
    """
    checks = []

    key = settings.SECRET_KEY or ""
    weak = (not key or len(key) < 32 or "change" in key.lower()
            or "placeholder" in key.lower())
    checks.append({
        "name": "SECRET_KEY",
        "ok": not weak,
        "detail": ("Strong, and unique to this install." if not weak else
                   "Weak or default. It signs session cookies AND QR tokens, "
                   "so anyone who guesses it can forge check-ins."),
    })

    https = settings.BASE_URL.startswith("https://")
    checks.append({
        "name": "BASE_URL uses HTTPS",
        "ok": https,
        "detail": (f"{settings.BASE_URL}" if https else
                   f"{settings.BASE_URL} is not HTTPS. Phone cameras and "
                   "geolocation will refuse to work for students."),
    })

    checks.append({
        "name": "DEBUG disabled",
        "ok": not settings.DEBUG,
        "detail": ("Off, as it should be in production." if not settings.DEBUG
                   else "DEBUG is on. Turn it off outside local development."),
    })

    # Backups: recency is the only thing worth asserting, because a backup
    # job that silently stopped looks exactly like one that never ran.
    newest, age = None, None
    bdir = pathlib.Path("backups")
    if bdir.is_dir():
        files = sorted(bdir.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            newest = files[0]
            age = (dt.datetime.now()
                   - dt.datetime.fromtimestamp(newest.stat().st_mtime)).days
    checks.append({
        "name": "Recent database backup",
        "ok": age is not None and age <= 2,
        "detail": (f"{newest.name}, {age} day(s) old." if newest else
                   "No backup found. Schedule backup_db.py nightly."),
    })
    return checks


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    me = await require_admin(db, user)
    if not me:
        return FORBIDDEN

    instructors = (await db.execute(
        select(Instructor).order_by(Instructor.name)
    )).scalars().all()

    # Course COUNTS only -- never course contents. An admin may see that a
    # colleague teaches three courses; they may not see who is in them.
    counts = dict((await db.execute(
        select(CourseEnrollment.instructor_id, func.count(Course.id))
        .join(Course, Course.id == CourseEnrollment.course_id)
        .where(CourseEnrollment.role == "instructor")
        .group_by(CourseEnrollment.instructor_id)
    )).all())

    institution = (await db.execute(
        select(Institution).order_by(Institution.id)
    )).scalars().first()

    # Request first: this Starlette expects TemplateResponse(request, name,
    # context). Passing the name first makes the context dict land in the
    # cache key and fails with an unhashable-type TypeError at render time.
    return templates.TemplateResponse(request, "system/admin.html", {
        # base.html renders the whole navigation from `user`; without it the
        # page loses its nav entirely, including the link back to Dashboard.
        "user": user,
        "me": me,
        "instructors": instructors,
        "course_counts": counts,
        "institution": institution,
        "checks": _install_health(),
        "base_url": settings.BASE_URL.rstrip("/"),
        "settings": settings,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/instructors/add")
async def add_instructor(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    is_admin_flag: bool = Form(False),
):
    user = get_current_user(request)
    if not await require_admin(db, user):
        return FORBIDDEN

    email = email.strip().lower()
    name = name.strip()
    if not name or not email or len(password) < 12:
        return RedirectResponse(
            "/admin?error=Name,+email+and+a+password+of+at+least+12+characters"
            "+are+required.", status_code=302)

    existing = (await db.execute(
        select(Instructor).where(Instructor.email == email)
    )).scalar_one_or_none()
    if existing:
        return RedirectResponse("/admin?error=That+email+already+has+an+account.",
                                status_code=302)

    db.add(Instructor(name=name, email=email,
                      password_hash=hash_password(password),
                      is_admin=is_admin_flag, is_active=True))
    await db.commit()
    return RedirectResponse(f"/admin?saved=Added+{email}", status_code=302)


@router.post("/instructors/{instructor_id}/toggle-admin")
async def toggle_admin(
    request: Request,
    instructor_id: int,
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    me = await require_admin(db, user)
    if not me:
        return FORBIDDEN

    target = await db.get(Instructor, instructor_id)
    if not target:
        return RedirectResponse("/admin?error=No+such+account.", status_code=302)

    # Removing your own admin rights would lock the installation out of its
    # own administration with no way back except SQL.
    if target.id == me.id:
        return RedirectResponse(
            "/admin?error=You+cannot+change+your+own+admin+rights.+Ask+another"
            "+admin.", status_code=302)

    target.is_admin = not target.is_admin
    await db.commit()
    verb = "now+an+admin" if target.is_admin else "no+longer+an+admin"
    return RedirectResponse(f"/admin?saved={target.email}+is+{verb}", status_code=302)


@router.post("/instructors/{instructor_id}/toggle-active")
async def toggle_active(
    request: Request,
    instructor_id: int,
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    me = await require_admin(db, user)
    if not me:
        return FORBIDDEN

    target = await db.get(Instructor, instructor_id)
    if not target:
        return RedirectResponse("/admin?error=No+such+account.", status_code=302)
    if target.id == me.id:
        return RedirectResponse("/admin?error=You+cannot+deactivate+your+own+account.",
                                status_code=302)

    # Deactivate, never delete: their courses and attendance history are
    # referenced by grades already imported into D2L.
    target.is_active = not target.is_active
    await db.commit()
    verb = "reactivated" if target.is_active else "deactivated"
    return RedirectResponse(f"/admin?saved={target.email}+{verb}", status_code=302)


@router.post("/instructors/{instructor_id}/reset-password")
async def reset_password(
    request: Request,
    instructor_id: int,
    db: AsyncSession = Depends(get_db),
    new_password: str = Form(...),
):
    user = get_current_user(request)
    if not await require_admin(db, user):
        return FORBIDDEN

    target = await db.get(Instructor, instructor_id)
    if not target:
        return RedirectResponse("/admin?error=No+such+account.", status_code=302)
    if len(new_password) < 12:
        return RedirectResponse("/admin?error=Password+must+be+at+least+12+characters.",
                                status_code=302)

    target.password_hash = hash_password(new_password)
    await db.commit()
    return RedirectResponse(f"/admin?saved=Password+reset+for+{target.email}",
                            status_code=302)


@router.post("/d2l")
async def save_d2l(
    request: Request,
    db: AsyncSession = Depends(get_db),
    # Optional, not "": absent must stay distinguishable from cleared, or a
    # partial POST silently erases live credentials and breaks every launch.
    d2l_host: Optional[str] = Form(None),
    lti_consumer_key: Optional[str] = Form(None),
    lti_consumer_secret: Optional[str] = Form(None),
):
    user = get_current_user(request)
    if not await require_admin(db, user):
        return FORBIDDEN

    institution = (await db.execute(
        select(Institution).order_by(Institution.id)
    )).scalars().first()
    if not institution:
        return RedirectResponse("/admin?error=No+institution+record+exists.",
                                status_code=302)

    if d2l_host and d2l_host.strip():
        institution.d2l_host = d2l_host.strip()
    if lti_consumer_key and lti_consumer_key.strip():
        institution.lti_consumer_key = lti_consumer_key.strip()
    if lti_consumer_secret and lti_consumer_secret.strip():
        institution.lti_consumer_secret = lti_consumer_secret.strip()
    elif institution.lti_consumer_key and not institution.lti_consumer_secret:
        institution.lti_consumer_secret = secrets.token_urlsafe(36)

    await db.commit()
    return RedirectResponse(
        "/admin?saved=D2L+connection+updated.+Every+course+on+this+install+uses"
        "+it,+so+confirm+a+launch+still+works.", status_code=302)
