"""Instructor-facing routes — dashboard, sessions, analytics, export."""

from __future__ import annotations

import csv
import datetime as dt
import io
import time as _time
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import ledger
from app.auth import (
    clear_session_cookie,
    get_current_user,
    set_session_cookie,
    verify_password,
)
from app.authz import is_admin, owned_course, owned_session
from app.config import settings
from app.database import get_db
from app.main import _to_local, templates
from app.models import (
    AttendanceRecord,
    Course,
    CourseEnrollment,
    Institution,
    Instructor,
    Session,
    Student,
)
from app.scoring import (
    DEFAULT_ALLOWED_UNEXCUSED,
    DEFAULT_LATE_CREDIT_PERCENT,
    DEFAULT_LATE_THRESHOLD_MINUTES,
    EXCUSED_STATUS,
    PRESENT_STATUSES,
    policy_for,
    score,
)

router = APIRouter(prefix="/instructor", tags=["instructor"])


# ─── Auth helpers ───────────────────────────────────────────

def require_instructor(request: Request) -> dict:
    user = get_current_user(request)
    if not user or user.get("role") != "instructor":
        return None
    return user


# ─── Login / Logout ────────────────────────────────────────

# Simple brute-force throttle: 5 failed attempts per IP -> 10-minute lockout.
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT_SECONDS = 600
_login_fails: dict = {}  # ip -> [fail_count, lockout_until_epoch]


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request)
    now = _time.time()
    state = _login_fails.get(ip)
    if state and state[1] > now:
        return HTMLResponse(
            "<h2>Too many login attempts</h2><p>Please try again in 10 minutes.</p>",
            status_code=429,
        )

    stmt = select(Instructor).where(Instructor.email == email)
    result = await db.execute(stmt)
    instructor = result.scalar_one_or_none()
    if instructor and not instructor.is_active:
        # Same generic failure path as a bad password: do not confirm that an
        # account exists to someone who cannot use it.
        instructor = None
    if instructor and verify_password(password, instructor.password_hash):
        _login_fails.pop(ip, None)
        resp = RedirectResponse("/instructor/dashboard", status_code=302)
        set_session_cookie(resp, {
            "role": "instructor",
            "instructor_id": instructor.id,
            "name": instructor.name,
            # Carried in the signed cookie purely so the navigation can show
            # the Admin link without a database query on every page render.
            # It is never trusted for authorization: every admin route
            # re-checks the database via require_admin, so a cookie left over
            # from revoked rights renders a link that then returns 403.
            "is_admin": bool(instructor.is_admin),
        })
        return resp

    state = _login_fails.get(ip) or [0, 0.0]
    state[0] += 1
    if state[0] >= LOGIN_MAX_FAILS:
        state = [0, now + LOGIN_LOCKOUT_SECONDS]
    _login_fails[ip] = state
    return templates.TemplateResponse(request, "login.html", {
        "error": "Invalid email or password.",
    }, status_code=401)


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    clear_session_cookie(resp)
    return resp


# ─── Dashboard ──────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    instructor_id = user["instructor_id"]

    # Courses this instructor teaches
    stmt = (
        select(Course)
        .join(CourseEnrollment)
        .where(
            CourseEnrollment.instructor_id == instructor_id,
            CourseEnrollment.role == "instructor",
        )
    )
    courses = (await db.execute(stmt)).scalars().all()

    # Active sessions
    active_sessions = {}
    for c in courses:
        sess_stmt = select(Session).where(
            Session.course_id == c.id,
            Session.ended_at.is_(None),
        )
        sess = (await db.execute(sess_stmt)).scalar_one_or_none()
        if sess:
            count_stmt = select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.session_id == sess.id
            )
            count = (await db.execute(count_stmt)).scalar() or 0
            active_sessions[c.id] = {"session": sess, "count": count}

    # Early alerts: students below 75% and consecutive absences
    alerts = []
    for c in courses:
        alerts.extend(await _compute_alerts(db, c))

    return templates.TemplateResponse(request, "instructor/dashboard.html", {
        "user": user,
        "courses": courses,
        "active_sessions": active_sessions,
        "alerts": alerts,
    })


async def _compute_alerts(db: AsyncSession, course: Course) -> list:
    """Return alert dicts for students with poor attendance."""
    _policy = policy_for(course)
    alerts = []
    # Total sessions for this course
    total_stmt = select(func.count(Session.id)).where(Session.course_id == course.id)
    total_sessions = (await db.execute(total_stmt)).scalar() or 0
    if total_sessions == 0:
        return alerts

    # Enrolled students
    enr_stmt = (
        select(Student)
        .join(CourseEnrollment)
        .where(CourseEnrollment.course_id == course.id, CourseEnrollment.role == "student")
    )
    students = (await db.execute(enr_stmt)).scalars().all()

    # All sessions ordered by date
    sess_stmt = select(Session).where(Session.course_id == course.id).order_by(Session.started_at)
    sessions = (await db.execute(sess_stmt)).scalars().all()
    session_ids = [s.id for s in sessions]

    for student in students:
        att_stmt = select(AttendanceRecord).where(
            AttendanceRecord.student_id == student.id,
            AttendanceRecord.session_id.in_(session_ids),
            AttendanceRecord.status.in_(["present", "late"]),
        )
        records = (await db.execute(att_stmt)).scalars().all()
        attended_session_ids = {r.session_id for r in records}
        attended = len(attended_session_ids)
        # Split by status: a late arrival may be worth only part of a class,
        # so present and late cannot be collapsed into one count any more.
        _late = len({r.session_id for r in records if r.status == "late"})
        _present = max(0, attended - _late)
        _exc = await _excused_count(db, student.id, session_ids)
        pct = score(total_sessions, _present, _late, _exc,
                    _policy.allowed_unexcused, _policy.late_credit_percent).pct

        # Consecutive absences (from most recent)
        consecutive = 0
        for sid in reversed(session_ids):
            if sid not in attended_session_ids:
                consecutive += 1
            else:
                break

        if pct < 75 or consecutive >= 3:
            alerts.append({
                "student": student,
                "course": course,
                "pct": pct,
                "attended": attended,
                "total": total_sessions,
                "consecutive_absences": consecutive,
            })
    return alerts


# ─── Course detail ──────────────────────────────────────────

@router.get("/course/{course_id}", response_class=HTMLResponse)
async def course_detail(
    request: Request,
    course_id: int,
    db: AsyncSession = Depends(get_db),
    import_new: Optional[int] = None,
    import_updated: Optional[int] = None,
    import_enrolled: Optional[int] = None,
):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Course not found", status_code=404)
    _policy = policy_for(course)

    # Sessions
    sess_stmt = select(Session).where(Session.course_id == course_id).order_by(Session.started_at.desc())
    sessions = (await db.execute(sess_stmt)).scalars().all()

    # Enrolled students with attendance stats
    enr_stmt = (
        select(Student)
        .join(CourseEnrollment)
        .where(CourseEnrollment.course_id == course_id, CourseEnrollment.role == "student")
        .order_by(Student.name)
    )
    students = (await db.execute(enr_stmt)).scalars().all()
    total_sessions = len(sessions)

    student_stats = []
    session_ids = [s.id for s in sessions]
    for s in students:
        if session_ids:
            att_stmt = select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.student_id == s.id,
                AttendanceRecord.session_id.in_(session_ids),
                AttendanceRecord.status.in_(["present", "late"]),
            )
            attended = (await db.execute(att_stmt)).scalar() or 0
            _late = (await db.execute(select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.student_id == s.id,
                AttendanceRecord.session_id.in_(session_ids),
                AttendanceRecord.status == "late",
            ))).scalar() or 0
        else:
            attended = 0
            _late = 0
        _present = max(0, attended - _late)
        _exc = await _excused_count(db, s.id, session_ids)
        _sc = score(total_sessions, _present, _late, _exc,
                    _policy.allowed_unexcused, _policy.late_credit_percent)
        student_stats.append({"student": s, "attended": attended, "total": total_sessions,
                              "late": _late,
                              "pct": _sc.pct, "excused": _sc.excused,
                              "unexcused": _sc.unexcused, "forgiven": _sc.forgiven,
                              "over_limit": _sc.unexcused_over_limit})

    # Build import result from query params (set after redirect from import)
    import_result = None
    if import_new is not None:
        import_result = {
            "new": import_new,
            "updated": import_updated or 0,
            "enrolled": import_enrolled or 0,
        }

    return templates.TemplateResponse(request, "instructor/course_detail.html", {
        "user": user,
        "course": course,
        "sessions": sessions,
        "student_stats": student_stats,
        "import_result": import_result,
        "allowed_absences": _policy.allowed_unexcused,
        "policy": _policy,
    })


# ─── Start / Stop session ──────────────────────────────────

@router.post("/course/{course_id}/start-session")
async def start_session(request: Request, course_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    if not await owned_course(db, user, course_id):
        return RedirectResponse("/instructor/dashboard", status_code=302)

    # Check for existing active session
    existing = (await db.execute(
        select(Session).where(Session.course_id == course_id, Session.ended_at.is_(None))
    )).scalar_one_or_none()
    if existing:
        return RedirectResponse(f"/instructor/session/{existing.id}", status_code=302)

    session = Session(
        course_id=course_id,
        started_by_instructor_id=user["instructor_id"],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return RedirectResponse(f"/instructor/session/{session.id}", status_code=302)


@router.post("/session/{session_id}/stop")
async def stop_session(request: Request, session_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    session, course = await owned_session(db, user, session_id)
    if not session:
        return RedirectResponse("/instructor/dashboard", status_code=302)
    if session.ended_at is None:
        session.ended_at = dt.datetime.utcnow()
        await db.commit()
    return RedirectResponse(f"/instructor/course/{session.course_id}", status_code=302)


@router.post("/session/{session_id}/mark-present")
async def mark_present(
    request: Request,
    session_id: int,
    db: AsyncSession = Depends(get_db),
    student_id: int = Form(...),
    status: str = Form("present"),
):
    """Instructor marks a student by hand (dead phone, GPS denied, late arrival).

    Takes a status because this is the real decision point for a late
    arrival. The QR code is normally only projected at the start of class, so
    somebody who walks in at ten past cannot scan at all -- they talk to the
    instructor, who marks them here and chooses what that is worth. An
    automatic clock-based "late" almost never fires in practice.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    session, course = await owned_session(db, user, session_id)
    if not session:
        return RedirectResponse(f"/instructor/session/{session_id}", status_code=302)

    # Whitelist, never the raw form value: anything else would become an
    # unknown status that scoring silently treats as an absence.
    if status not in ("present", "late", "excused"):
        status = "present"

    # Deliberately allowed on an ENDED session: students routinely sort out a
    # missed check-in after class, and the roll would otherwise be frozen wrong.
    existing = (await db.execute(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.student_id == student_id,
    ))).scalar_one_or_none()
    if existing:
        # Correcting an earlier mark is the common case -- an instructor who
        # clicked Present then learns the student was late should not have to
        # unmark first.
        existing.status = status
    else:
        db.add(AttendanceRecord(
            session_id=session_id, student_id=student_id,
            token_used="manual", status=status,
        ))
    await db.commit()
    return RedirectResponse(f"/instructor/session/{session_id}", status_code=302)


# ─── Live session view ──────────────────────────────────────

@router.post("/session/{session_id}/unmark")
async def unmark_attendance(request: Request, session_id: int,
                            db: AsyncSession = Depends(get_db),
                            student_id: int = Form(...)):
    """Remove an attendance record -- the undo for a mistaken or proxy check-in.

    Works on ended sessions too, for the same reason mark_present does.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    session, course = await owned_session(db, user, session_id)
    if not session:
        return HTMLResponse("Not found", status_code=404)
    await db.execute(delete(AttendanceRecord).where(and_(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.student_id == student_id)))
    await db.commit()
    return RedirectResponse(f"/instructor/session/{session_id}", status_code=302)


@router.post("/session/{session_id}/mark-excused")
async def mark_excused(request: Request, session_id: int,
                       db: AsyncSession = Depends(get_db),
                       student_id: int = Form(...)):
    """Mark a student excused for this session.

    An excused absence is dropped from the denominator entirely, so it neither
    helps nor hurts, and it does not consume one of the free unexcused
    absences. Works on ended sessions, since documentation usually arrives
    after class.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    session, course = await owned_session(db, user, session_id)
    if not session:
        return HTMLResponse("Not found", status_code=404)
    existing = (await db.execute(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.student_id == student_id,
    ))).scalar_one_or_none()
    if existing:
        existing.status = EXCUSED_STATUS
        existing.token_used = "manual"
    else:
        db.add(AttendanceRecord(session_id=session_id, student_id=student_id,
                                token_used="manual", status=EXCUSED_STATUS))
    await db.commit()
    return RedirectResponse(f"/instructor/session/{session_id}", status_code=302)


@router.get("/session/{session_id}", response_class=HTMLResponse)
async def session_view(request: Request, session_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    session, course = await owned_session(db, user, session_id)
    if not session:
        return HTMLResponse("Session not found", status_code=404)

    # Current attendance
    att_stmt = (
        select(AttendanceRecord)
        .options(selectinload(AttendanceRecord.student))
        .where(AttendanceRecord.session_id == session_id)
        .order_by(AttendanceRecord.checked_in_at.desc())
    )
    records = (await db.execute(att_stmt)).scalars().all()

    # Total enrolled
    enr_count = (await db.execute(
        select(func.count(CourseEnrollment.id)).where(
            CourseEnrollment.course_id == course.id,
            CourseEnrollment.role == "student",
        )
    )).scalar() or 0

    # Students not yet checked in (for manual mark-present)
    checked_ids = {r.student_id for r in records}
    enrolled_students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course.id,
            CourseEnrollment.role == "student",
        ).order_by(Student.name)
    )).scalars().all()
    missing_students = [s for s in enrolled_students if s.id not in checked_ids]

    return templates.TemplateResponse(request, "instructor/session.html", {
        "user": user,
        "session": session,
        "course": course,
        "records": records,
        "enrolled_count": enr_count,
        "missing_students": missing_students,
        "qr_rotation_seconds": settings.QR_ROTATION_SECONDS,
        # So the Late button can say what it will cost the student.
        "policy": policy_for(course),
    })


# ─── Analytics ──────────────────────────────────────────────

@router.get("/course/{course_id}/analytics", response_class=HTMLResponse)
async def analytics(request: Request, course_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Course not found", status_code=404)

    sess_stmt = select(Session).where(Session.course_id == course_id).order_by(Session.started_at)
    sessions = (await db.execute(sess_stmt)).scalars().all()

    # Per-session attendance counts
    session_labels = []
    session_counts = []
    for s in sessions:
        ct = (await db.execute(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.session_id == s.id,
                AttendanceRecord.status.in_(["present", "late"]),
            )
        )).scalar() or 0
        label = _to_local(s.started_at).strftime("%m/%d") if s.started_at else "?"
        session_labels.append(label)
        session_counts.append(ct)

    # Enrolled count
    enr_count = (await db.execute(
        select(func.count(CourseEnrollment.id)).where(
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.role == "student",
        )
    )).scalar() or 0

    return templates.TemplateResponse(request, "instructor/analytics.html", {
        "user": user,
        "course": course,
        "session_labels": session_labels,
        "session_counts": session_counts,
        "enrolled_count": enr_count,
    })


# ─── CSV Export ─────────────────────────────────────────────

@router.get("/course/{course_id}/export")
async def export_csv(request: Request, course_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)

    sessions = (await db.execute(
        select(Session).where(Session.course_id == course_id).order_by(Session.started_at)
    )).scalars().all()

    students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.role == "student",
        ).order_by(Student.name)
    )).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)

    header = ["Student ID", "Name"]
    for s in sessions:
        header.append(_to_local(s.started_at).strftime("%Y-%m-%d %H:%M") if s.started_at else "Unknown")
    header.append("Total Present")
    header.append("Excused")
    header.append("Unexcused Absences")
    header.append(f"Attendance % (first {_policy.allowed_unexcused} unexcused forgiven)")
    writer.writerow(header)

    session_ids = [s.id for s in sessions]
    for student in students:
        row = [student.student_id_number, student.name]
        present_count = 0
        excused_count = 0
        late_count = 0
        for sid in session_ids:
            rec = (await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == sid,
                    AttendanceRecord.student_id == student.id,
                )
            )).scalar_one_or_none()
            if rec and rec.status == "late":
                row.append("Late")
                late_count += 1
            elif rec and rec.status in PRESENT_STATUSES:
                row.append(rec.status.capitalize())
                present_count += 1
            elif rec and rec.status == EXCUSED_STATUS:
                row.append("Excused")
                excused_count += 1
            else:
                row.append("Absent")
        row.append(str(present_count + late_count))
        _sc = score(len(sessions), present_count, late_count, excused_count,
                    _policy.allowed_unexcused, _policy.late_credit_percent)
        row.append(str(_sc.excused))
        row.append(str(_sc.unexcused))
        row.append(f"{_sc.pct}%")
        writer.writerow(row)

    buf.seek(0)
    filename = f"{course.course_code or 'course'}_attendance.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Course Settings ────────────────────────────────────────

@router.get("/course/{course_id}/settings", response_class=HTMLResponse)
async def course_settings(request: Request, course_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)
    institution = await db.get(Institution, course.institution_id) if course.institution_id else None
    return templates.TemplateResponse(request, "admin/settings.html", {
        "user": user,
        "course": course,
        "institution": institution,
        "is_admin": await is_admin(db, user),
        "base_url": settings.BASE_URL.rstrip("/"),
        # Shown as placeholders so an instructor can see what "leave blank"
        # will actually give them.
        "default_allowed_absences": DEFAULT_ALLOWED_UNEXCUSED,
        "default_late_threshold": DEFAULT_LATE_THRESHOLD_MINUTES,
        "default_late_credit": DEFAULT_LATE_CREDIT_PERCENT,
    })


@router.post("/course/{course_id}/settings")
async def save_course_settings(
    request: Request,
    course_id: int,
    db: AsyncSession = Depends(get_db),
    # Default None, not "": it lets us tell "the form did not include this
    # field" from "the user cleared it on purpose". With "" the two are
    # indistinguishable and any partial POST silently erases course identity
    # -- which is how the D2L deep link was wiped by an unrelated test.
    name: Optional[str] = Form(None),
    course_code: Optional[str] = Form(None),
    d2l_org_unit_id: Optional[str] = Form(None),
    d2l_attendance_checkin_url: Optional[str] = Form(None),
    geofence_lat: Optional[float] = Form(None),
    geofence_lon: Optional[float] = Form(None),
    geofence_radius_m: Optional[float] = Form(None),
    geofence_enabled: bool = Form(False),
    allowed_ip_ranges: Optional[str] = Form(None),
    # Empty string means "fall back to the institution default", which is why
    # these are strings rather than ints: a blank number field posts "".
    policy_section: Optional[str] = Form(None),
    allowed_unexcused_absences: Optional[str] = Form(None),
    late_threshold_minutes: Optional[str] = Form(None),
    late_credit_percent: Optional[str] = Form(None),
):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)

    # Course details. A field absent from the POST is left alone; a field
    # present but empty is an explicit clear. Name is required either way, so
    # a blank name never wipes the course's name.
    if name is not None and name.strip():
        course.name = name.strip()
    if course_code is not None:
        course.course_code = course_code.strip() or None
    if d2l_org_unit_id is not None:
        course.d2l_org_unit_id = d2l_org_unit_id.strip() or None
    if d2l_attendance_checkin_url is not None:
        course.d2l_attendance_checkin_url = d2l_attendance_checkin_url.strip() or None

    # The geofence block always posts together, and the checkbox is absent
    # when unticked, so these are applied as submitted.
    course.geofence_lat = geofence_lat
    course.geofence_lon = geofence_lon
    if geofence_radius_m is not None:
        course.geofence_radius_m = geofence_radius_m
    course.geofence_enabled = geofence_enabled
    if allowed_ip_ranges is not None:
        course.allowed_ip_ranges = allowed_ip_ranges.strip() or None

    # Attendance policy. Blank clears the override and returns the course to
    # the institution default, which is the behaviour the placeholder promises.
    def _optional_int(raw: Optional[str]) -> Optional[int]:
        if raw is None or not raw.strip():
            return None
        try:
            return max(0, int(raw.strip()))
        except ValueError:
            return None

    if policy_section is not None:
        # The block was on the submitted form, so blanks are deliberate and
        # mean "inherit the institution default".
        course.allowed_unexcused_absences = _optional_int(allowed_unexcused_absences)
        course.late_threshold_minutes = _optional_int(late_threshold_minutes)
        # Blank means "use the default", same as the other two fields.
        _lc = _optional_int(late_credit_percent)
        course.late_credit_percent = None if _lc is None else max(0, min(100, _lc))

    # Institution-level D2L settings are deliberately NOT accepted here. They
    # are shared by every course, so a global change must not happen inside a
    # single course's URL where its blast radius is invisible. They live at
    # /admin instead.

    await db.commit()
    return RedirectResponse(f"/instructor/course/{course_id}/settings", status_code=302)


@router.post("/course/{course_id}/reset-data")
async def reset_course_data(request: Request, course_id: int, db: AsyncSession = Depends(get_db)):
    """Delete all sessions and attendance records for a course. Roster and settings are kept."""
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)
    session_ids = select(Session.id).where(Session.course_id == course_id)
    await db.execute(delete(AttendanceRecord).where(AttendanceRecord.session_id.in_(session_ids)))
    await db.execute(delete(Session).where(Session.course_id == course_id))
    await db.commit()
    return RedirectResponse(f"/instructor/course/{course_id}?reset=1", status_code=302)


@router.post("/course/{course_id}/remove-student/{student_id}")
async def remove_student_from_roster(request: Request, course_id: int, student_id: int,
                                     db: AsyncSession = Depends(get_db)):
    """Unenroll a student from this course and drop their attendance for it.

    Needed for drops, mis-imports, and phantom records (e.g. an instructor who
    once launched the tool with a learner role). Scoped to this course only --
    the student row itself is kept in case other courses reference it.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)
    session_ids = select(Session.id).where(Session.course_id == course_id)
    await db.execute(delete(AttendanceRecord).where(and_(
        AttendanceRecord.student_id == student_id,
        AttendanceRecord.session_id.in_(session_ids))))
    await db.execute(delete(CourseEnrollment).where(and_(
        CourseEnrollment.course_id == course_id,
        CourseEnrollment.student_id == student_id)))
    await db.commit()
    return RedirectResponse(f"/instructor/course/{course_id}", status_code=302)


@router.post("/course/{course_id}/edit-student/{student_id}")
async def edit_student(request: Request, course_id: int, student_id: int,
                       db: AsyncSession = Depends(get_db),
                       name: str = Form(""),
                       student_id_number: str = Form(""),
                       d2l_username: str = Form("")):
    """Correct a student's details.

    The D2L username is what the gradebook export matches on, so a typo or a
    name change means the grade silently fails to land. This is the fix-it path
    that avoids re-importing the whole roster.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    if not await owned_course(db, user, course_id):
        return HTMLResponse("Not found", status_code=404)
    enr = (await db.execute(select(CourseEnrollment).where(and_(
        CourseEnrollment.course_id == course_id,
        CourseEnrollment.student_id == student_id)))).scalar_one_or_none()
    if not enr:
        return HTMLResponse("Student is not on this roster", status_code=404)
    student = await db.get(Student, student_id)
    if not student:
        return HTMLResponse("Not found", status_code=404)

    if name.strip():
        student.name = name.strip()
    new_sid = student_id_number.strip()
    if new_sid and new_sid != student.student_id_number:
        # student_id_number is UNIQUE; refuse a collision instead of 500ing.
        clash = (await db.execute(select(Student).where(and_(
            Student.student_id_number == new_sid,
            Student.id != student_id)))).scalar_one_or_none()
        if clash:
            return RedirectResponse(
                f"/instructor/course/{course_id}?error=Student+ID+{new_sid}+is+already+used+by+{clash.name}",
                status_code=302)
        student.student_id_number = new_sid
    student.d2l_username = d2l_username.strip() or None
    await db.commit()
    return RedirectResponse(f"/instructor/course/{course_id}", status_code=302)



async def _excused_count(db: AsyncSession, student_id: int, session_ids: list) -> int:
    """How many of this course's sessions the student was excused from."""
    if not session_ids:
        return 0
    return (await db.execute(
        select(func.count(AttendanceRecord.id)).where(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.session_id.in_(session_ids),
            AttendanceRecord.status == EXCUSED_STATUS,
        ))).scalar() or 0


@router.post("/course/{course_id}/restore-from-d2l")
async def restore_from_d2l(request: Request, course_id: int,
                           db: AsyncSession = Depends(get_db),
                           grades_file: UploadFile = File(...)):
    """Rebuild this course's attendance from a D2L grade export.

    This is what lets D2L be the system of record. The Attendance Detail text
    column carries every class date and status, so a grade export is a
    complete backup. Upload it here and the sessions and attendance records
    are recreated.

    Restoring is additive and idempotent: a session is matched to an existing
    one on the same date, and a student's record for a session is only
    created if it is missing. Running it twice changes nothing, and it never
    deletes what the app already has.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)

    raw = (await grades_file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    headers = next(reader, None)
    if not headers:
        return RedirectResponse(
            f"/instructor/course/{course_id}?error=That+file+is+empty.", status_code=302)

    detail_idx = ledger.find_detail_column(headers)
    user_idx = ledger.find_user_column(headers)
    if detail_idx is None or user_idx is None:
        return RedirectResponse(
            f"/instructor/course/{course_id}?error=No+%22Attendance+Detail%22+column+found."
            "+In+D2L+use+Grades+%E2%86%92+Export+and+tick+Attendance+Detail.",
            status_code=302)

    # Existing sessions by local date, so a restore lines up with reality.
    existing = (await db.execute(
        select(Session).where(Session.course_id == course_id))).scalars().all()
    by_date = {}
    for s in existing:
        if s.started_at:
            by_date[_to_local(s.started_at).date()] = s

    students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.role == "student"))).scalars().all()
    by_username = {}
    for st in students:
        for key in (st.d2l_username, st.student_id_number,
                    (st.email or "").split("@")[0] or None):
            if key:
                by_username.setdefault(key.strip().lower(), st)

    sessions_made = records_made = 0
    unknown_users = []

    for row in reader:
        if len(row) <= max(detail_idx, user_idx):
            continue
        uname = (row[user_idx] or "").strip().strip('"')
        entries = ledger.parse(row[detail_idx] or "")
        if not entries:
            continue
        student = by_username.get(uname.lower())
        if not student:
            if uname:
                unknown_users.append(uname)
            continue

        for day, status in entries:
            session = by_date.get(day)
            if session is None:
                # Noon local, converted back to the naive UTC the app stores.
                local_noon = dt.datetime(day.year, day.month, day.day, 12, 0)
                offset = settings.DISPLAY_UTC_OFFSET_HOURS
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
                    started = local_noon.replace(tzinfo=tz).astimezone(
                        dt.timezone.utc).replace(tzinfo=None)
                except Exception:
                    started = local_noon - dt.timedelta(hours=offset)
                session = Session(course_id=course_id, started_at=started,
                                  ended_at=started + dt.timedelta(hours=1))
                db.add(session)
                await db.flush()
                by_date[day] = session
                sessions_made += 1

            if status is None:      # absent: represented by the lack of a record
                continue
            dupe = (await db.execute(select(AttendanceRecord).where(
                AttendanceRecord.session_id == session.id,
                AttendanceRecord.student_id == student.id))).scalar_one_or_none()
            if dupe:
                continue
            db.add(AttendanceRecord(session_id=session.id, student_id=student.id,
                                    token_used="restored", status=status))
            records_made += 1

    await db.commit()

    msg = f"Restored from D2L: {sessions_made} sessions and {records_made} attendance records added."
    if unknown_users:
        shown = ", ".join(sorted(set(unknown_users))[:5])
        msg += f" Skipped {len(set(unknown_users))} unmatched username(s): {shown}."
    return RedirectResponse(
        f"/instructor/course/{course_id}?notice={quote_plus(msg)}", status_code=302)


# ─── D2L Integration ────────────────────────────────────────

def _match_column(headers: list[str], *candidates: str) -> int | None:
    """Find a column index by case-insensitive partial match against candidates."""
    for idx, h in enumerate(headers):
        h_lower = h.lower().strip()
        for c in candidates:
            if c.lower() in h_lower:
                return idx
    return None


@router.post("/course/{course_id}/import-roster")
async def import_d2l_roster(
    request: Request,
    course_id: int,
    db: AsyncSession = Depends(get_db),
    roster_file: UploadFile = File(...),
):
    """Import a D2L Classlist CSV to sync the course roster."""
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Course not found", status_code=404)

    # Read and decode the uploaded CSV (handle BOM)
    raw_bytes = await roster_file.read()
    text = raw_bytes.decode("utf-8-sig")  # strips BOM automatically

    # Detect CSV dialect
    try:
        dialect = csv.Sniffer().sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    headers = next(reader, None)
    if not headers:
        return templates.TemplateResponse(request, "instructor/course_detail.html", {
            "user": user, "course": course, "sessions": [], "student_stats": [],
            "import_result": {"error": "CSV file is empty or has no header row."},
        })

    # Map columns flexibly
    col_first = _match_column(headers, "first name", "first")
    col_last = _match_column(headers, "last name", "last")
    col_orgid = _match_column(headers, "orgdefinedid", "org defined id", "student id")
    col_username = _match_column(headers, "username")
    col_email = _match_column(headers, "email")
    col_role = _match_column(headers, "role")

    if col_first is None and col_last is None:
        return templates.TemplateResponse(request, "instructor/course_detail.html", {
            "user": user, "course": course, "sessions": [], "student_stats": [],
            "import_result": {"error": "Could not find First Name / Last Name columns in the CSV."},
        })

    new_count = 0
    updated_count = 0
    enrolled_count = 0

    for row in reader:
        if not row or all(cell.strip() == "" for cell in row):
            continue

        # Skip non-student rows (e.g. instructors) if role column present
        if col_role is not None:
            role_val = row[col_role].strip().lower() if col_role < len(row) else ""
            if role_val and role_val not in ("learner", "student", ""):
                continue

        first_name = row[col_first].strip() if col_first is not None and col_first < len(row) else ""
        last_name = row[col_last].strip() if col_last is not None and col_last < len(row) else ""
        full_name = f"{first_name} {last_name}".strip()

        org_id = row[col_orgid].strip().lstrip("#") if col_orgid is not None and col_orgid < len(row) else ""
        username = row[col_username].strip() if col_username is not None and col_username < len(row) else ""
        email = row[col_email].strip() if col_email is not None and col_email < len(row) else ""

        if not org_id and not email:
            continue  # can't identify student without at least one identifier

        # Find existing student by OrgDefinedId or email
        student = None
        if org_id:
            stmt = select(Student).where(Student.student_id_number == org_id)
            student = (await db.execute(stmt)).scalar_one_or_none()
        if not student and email:
            stmt = select(Student).where(Student.email == email)
            student = (await db.execute(stmt)).scalar_one_or_none()

        if student:
            # Update existing student
            if full_name and student.name != full_name:
                student.name = full_name
            if email and student.email != email:
                student.email = email
            if org_id:
                student.d2l_user_id = org_id
            if username:
                student.d2l_username = username
            student.d2l_verified = True
            updated_count += 1
        else:
            # Create new student
            student = Student(
                name=full_name or username or org_id,
                email=email or None,
                student_id_number=org_id or username or email.split("@")[0],
                d2l_user_id=org_id or None,
                d2l_username=username or None,
                d2l_verified=True,
            )
            db.add(student)
            await db.flush()  # get student.id
            new_count += 1

        # Ensure enrollment exists
        enr_stmt = select(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.student_id == student.id,
        )
        existing_enr = (await db.execute(enr_stmt)).scalar_one_or_none()
        if not existing_enr:
            db.add(CourseEnrollment(
                course_id=course_id,
                student_id=student.id,
                role="student",
            ))
            enrolled_count += 1

    await db.commit()

    # Redirect back to course detail with flash-style query params
    return RedirectResponse(
        f"/instructor/course/{course_id}?import_new={new_count}&import_updated={updated_count}&import_enrolled={enrolled_count}",
        status_code=302,
    )


def _detail_ledger(student, sessions, statuses: dict, sc, _policy) -> str:
    """One-cell, human-readable record of every class meeting.

    Example:
      "9/11 present, 2 unexcused (3 allowed) | P=present L=late A=absent E=excused
       | 09/03:P 09/05:A 09/10:E ..."

    This is what lets D2L hold the full per-date record in a single column
    instead of one numeric column per meeting.
    """
    entries = []
    for s in sessions:
        if not s.started_at:
            continue
        entries.append((_to_local(s.started_at), statuses.get(s.id)))
    summary = (f"{sc.attended}/{sc.counted_total} present, "
               f"{sc.unexcused} unexcused ({_policy.allowed_unexcused} allowed)")
    # Say what a late arrival was worth, but only when it is not the default:
    # the cell goes into D2L and a student querying their grade should be able
    # to see the rule that produced it.
    if sc.late and not _policy.late_counts_fully:
        summary += f", {_policy.describe_late()}"
    if sc.excused:
        summary += f", {sc.excused} excused"
    return ledger.build(entries, summary)


@router.get("/course/{course_id}/export-d2l")
async def export_d2l(request: Request, course_id: int, db: AsyncSession = Depends(get_db),
                     detail: bool = True):
    """Export attendance as a D2L Brightspace grade import CSV.

    detail=1 adds a Text grade item carrying the full per-date ledger, so the
    gradebook holds the whole record in one extra column.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)
    _policy = policy_for(course)

    sessions = (await db.execute(
        select(Session).where(Session.course_id == course_id).order_by(Session.started_at)
    )).scalars().all()

    students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.role == "student",
        ).order_by(Student.name)
    )).scalars().all()

    total_sessions = len(sessions)
    session_ids = [s.id for s in sessions]

    # Refuse to hand D2L a file that can only destroy the record.
    # If this course has no sessions, every student would import as 0 and the
    # Attendance Detail ledger would be blanked -- wiping history that D2L is
    # holding. This is the failure mode after a reset or a lost database, so
    # it is blocked outright rather than warned about.
    if total_sessions == 0:
        return HTMLResponse(
            "<h2>Nothing to export</h2>"
            "<p>This course has no attendance sessions, so an export would "
            "overwrite the D2L gradebook with zeros and erase the attendance "
            "history stored there.</p>"
            "<p>If you expected sessions here, the app's data was reset or lost. "
            "Use <strong>Restore from D2L</strong> on the course page to rebuild "
            "it from the gradebook before exporting.</p>"
            f'<p><a href="/instructor/course/{course_id}">Back to course</a></p>',
            status_code=409)

    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["OrgDefinedId", "Username", "Attendance Points Grade"]
    if detail:
        # A D2L *Text* grade item. It holds a string, is not counted in the
        # final grade, and lets one column carry the whole per-date ledger --
        # so D2L keeps the full record without 45 numeric columns.
        header.append("Attendance Detail Text Grade")
    header.append("End-of-Line Indicator")
    writer.writerow(header)

    # Per-session status lookup, built once rather than per student.
    status_by_student: dict[int, dict[int, str]] = {}
    if detail and session_ids:
        recs = (await db.execute(select(AttendanceRecord).where(
            AttendanceRecord.session_id.in_(session_ids)))).scalars().all()
        for r in recs:
            status_by_student.setdefault(r.student_id, {})[r.session_id] = r.status

    for student in students:
        if session_ids:
            att_stmt = select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.student_id == student.id,
                AttendanceRecord.session_id.in_(session_ids),
                AttendanceRecord.status.in_(["present", "late"]),
            )
            attended = (await db.execute(att_stmt)).scalar() or 0
            _late = (await db.execute(select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.student_id == student.id,
                AttendanceRecord.session_id.in_(session_ids),
                AttendanceRecord.status == "late",
            ))).scalar() or 0
        else:
            attended = 0
            _late = 0

        _exc = await _excused_count(db, student.id, session_ids)
        sc = score(total_sessions, max(0, attended - _late), _late, _exc,
                   _policy.allowed_unexcused, _policy.late_credit_percent)
        pct = sc.pct

        # OrgDefinedId: prefer d2l_user_id, fall back to student_id_number
        org_id = student.d2l_user_id or student.student_id_number
        # Username: prefer d2l_username, then email prefix
        username = student.d2l_username or (student.email.split("@")[0] if student.email else "")

        row = [org_id, username, pct]
        if detail:
            row.append(_detail_ledger(student, sessions,
                                      status_by_student.get(student.id, {}), sc, _policy))
        row.append("#")
        writer.writerow(row)

    buf.seek(0)
    suffix = "_detail" if detail else ""
    filename = f"{course.course_code or 'course'}_attendance_d2l{suffix}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
