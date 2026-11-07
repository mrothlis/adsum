"""Student-facing routes — check-in, attendance history."""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.geo import check_geofence, check_ip_range
from app.main import templates
from app.models import (
    AttendanceRecord,
    Course,
    CourseEnrollment,
    Institution,
    Session,
    Student,
)
from app.qr import CODE_LENGTH, generate_token, resolve_code, verify_token
from app.scoring import policy_for

log = logging.getLogger("adsum.student")
router = APIRouter(prefix="/student", tags=["student"])

# Survives the round trip to D2L's login and back; holds only a session id.
PENDING_SESSION_COOKIE = "adsum_pending_session"

# Typing the code is a fallback, so a human pace is plenty. Without a limit
# the six-character space would be worth grinding at; with it, it is not.
#
# Only FAILED attempts count, and a success clears the key. A student who
# fat-fingers the code four times and then gets it right must not be held
# back by their own earlier typos, and the window is deliberately short so a
# mistyped code never turns into "come back in ten minutes" in front of a
# class. It is long enough to make 32^6 hopeless, which is the only job it has.
_CODE_FAILURES: dict[str, list[float]] = {}
_CODE_MAX_FAILURES = 10
_CODE_WINDOW_SECONDS = 30
_CODE_FAILURES_MAX_KEYS = 4096


def _code_attempts_exhausted(key: str) -> bool:
    """Has this address already failed too many times recently?"""
    now = time.time()
    recent = [t for t in _CODE_FAILURES.get(key, [])
              if now - t < _CODE_WINDOW_SECONDS]
    if recent:
        _CODE_FAILURES[key] = recent
    else:
        _CODE_FAILURES.pop(key, None)
    return len(recent) >= _CODE_MAX_FAILURES


def _record_code_failure(key: str) -> None:
    if len(_CODE_FAILURES) > _CODE_FAILURES_MAX_KEYS:
        _CODE_FAILURES.clear()
    _CODE_FAILURES.setdefault(key, []).append(time.time())


def _clear_code_failures(key: str) -> None:
    _CODE_FAILURES.pop(key, None)


# ─── Typed code fallback ──────────────────────────────────────
#
# For the student whose camera will not focus. The projected check-in URL
# carries a 64-character HMAC, so it is 133 characters long and nobody can
# reasonably type it -- which made the "camera not working" line a dead end.
# They type a short code instead, and it resolves to exactly the same token
# the QR would have given them, so every later step (enrolment, geofence,
# duplicate handling, the two-scan first-time flow) is unchanged.


@router.get("/code", response_class=HTMLResponse)
async def code_page(request: Request, bad: str = Query("")):
    """The page a student reaches by typing the bare domain."""
    return templates.TemplateResponse(request, "student/code.html", {
        "code_length": CODE_LENGTH,
        "error": bad,
    })


@router.post("/code")
async def submit_code(request: Request,
                      code: str = Form(""),
                      db: AsyncSession = Depends(get_db)):
    """Turn a typed code into the normal token check-in."""
    client = request.client.host if request.client else "unknown"
    if _code_attempts_exhausted(client):
        log.warning("code entry rate-limited for %s", client)
        return templates.TemplateResponse(request, "student/code.html", {
            "code_length": CODE_LENGTH,
            "error": "Too many wrong tries. Wait half a minute, then try again.",
        }, status_code=429)

    # Only sessions that are open right now are candidates, which keeps the
    # set tiny and means a code for a finished class resolves to nothing.
    open_ids = list((await db.execute(
        select(Session.id).where(Session.ended_at.is_(None))
    )).scalars().all())

    ok, session_id, reason = resolve_code(code, open_ids)
    if not ok or session_id is None:
        _record_code_failure(client)
        log.info("code entry refused for %s: %s", client, reason)
        return templates.TemplateResponse(request, "student/code.html", {
            "code_length": CODE_LENGTH,
            "error": reason,
        }, status_code=400)

    # Mint the token for the slot the code matched, then hand over to the
    # normal path. Nothing about check-in is reimplemented here.
    _clear_code_failures(client)
    token, _slot = generate_token(session_id)
    log.info("code entry accepted for session %s", session_id)
    return RedirectResponse(f"/student/checkin?token={token}", status_code=303)


# ─── Student check-in ─────────────────────────────────────────

@router.get("/checkin", response_class=HTMLResponse)
async def checkin_page(
    request: Request,
    token: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Page students land on after scanning the QR code.

    If they have an LTI session cookie, they're auto-identified.
    Otherwise, show a banner pointing them to launch from D2L first.
    """
    user = get_current_user(request)
    identified_student = None
    d2l_course_url = None
    geofence_required = False
    resumed_course = None

    if user and user.get("role") == "student":
        student = await db.get(Student, user["student_id"])
        if student:
            identified_student = student

    # Build the D2L course URL for the "Launch from D2L" button.
    valid, session_id, _ = verify_token(token)

    # Returning from D2L. A student who scans before signing in is sent to D2L
    # to authenticate, which takes far longer than a token stays valid, so the
    # token they arrived with is always dead by the time they come back. We
    # remember the class they were trying to join instead, and tell them
    # exactly what to do rather than failing at them. A fresh scan is still
    # required -- that is the whole point of the rotating code.
    if identified_student and not valid:
        pending = request.cookies.get(PENDING_SESSION_COOKIE)
        if pending and pending.isdigit():
            pending_session = await db.get(Session, int(pending))
            if pending_session and pending_session.ended_at is None:
                resumed_course = await db.get(Course, pending_session.course_id)

    if valid:
        session = await db.get(Session, session_id)
        if session:
            course = await db.get(Course, session.course_id)
            if course:
                geofence_required = bool(
                    course.geofence_enabled
                    and course.geofence_lat is not None
                    and course.geofence_lon is not None
                )
                # Prefer a stored deep link; otherwise build from institution + org unit.
                if course.d2l_attendance_checkin_url:
                    d2l_course_url = course.d2l_attendance_checkin_url
                elif course.d2l_org_unit_id:
                    institution = await db.get(Institution, course.institution_id)
                    if institution and institution.d2l_host:
                        d2l_course_url = (
                            f"https://{institution.d2l_host}"
                            f"/d2l/lms/content/main.d2l?ou={course.d2l_org_unit_id}"
                        )

    response = templates.TemplateResponse(request, "student/checkin.html", {
        "token": token,
        "identified_student": identified_student,
        "d2l_course_url": d2l_course_url,
        "geofence_required": geofence_required,
        "resumed_course": resumed_course,
    })

    if valid and not identified_student:
        # About to send them to D2L to sign in; remember the class so the trip
        # back is not a dead end.
        response.set_cookie(
            PENDING_SESSION_COOKIE, str(session_id),
            max_age=900, httponly=True, samesite="lax",
            secure=request.url.scheme == "https",
        )
    elif resumed_course is not None:
        response.delete_cookie(PENDING_SESSION_COOKIE)
    return response


@router.post("/checkin", response_class=HTMLResponse)
async def do_checkin(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: str = Form(""),
    gps_lat: Optional[float] = Form(None),
    gps_lon: Optional[float] = Form(None),
):
    """Process a student check-in — zero typing, auto-identified via LTI cookie."""
    status = "present"
    student = None
    distance_m = None

    # 1. Validate token
    valid, session_id, reason = verify_token(token)
    if not valid:
        return _checkin_response(request, token, errors=[reason])

    # 2. Look up session
    session = await db.get(Session, session_id)
    if not session or session.ended_at is not None:
        return _checkin_response(request, token, errors=["This attendance session has ended."])

    # 3. Identify student via LTI session cookie
    user = get_current_user(request)
    if not user or user.get("role") != "student":
        return _checkin_response(
            request, token,
            errors=["Please open this link in D2L on your phone first, then scan the QR."],
        )

    student = await db.get(Student, user["student_id"])
    if not student:
        return _checkin_response(
            request, token,
            errors=["Your D2L account isn't linked to a student record. Contact your instructor."],
        )

    # 4. Check enrollment
    enr_stmt = select(CourseEnrollment).where(
        CourseEnrollment.course_id == session.course_id,
        CourseEnrollment.student_id == student.id,
        CourseEnrollment.role == "student",
    )
    enrollment = (await db.execute(enr_stmt)).scalar_one_or_none()
    if not enrollment:
        return _checkin_response(request, token, errors=["You are not enrolled in this course."])

    # 5. Duplicate check
    dup_stmt = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.student_id == student.id,
    )
    existing = (await db.execute(dup_stmt)).scalar_one_or_none()
    if existing:
        return _checkin_response(
            request, token, success=True,
            message="You've already checked in for this session! ✅",
        )

    # There is no check-in time limit by design. The session accepts check-ins
    # until the instructor ends it; a student arriving late is marked present
    # manually after a conversation, rather than being silently locked out.

    # 6. Geofence — enforced only when the course enables it
    course = await db.get(Course, session.course_id)
    if course and course.geofence_enabled and course.geofence_lat is not None and course.geofence_lon is not None:
        if gps_lat is None or gps_lon is None:
            return _checkin_response(
                request, token,
                errors=["Location is required to check in. Allow location access, then tap Check In again."],
                identified_student=student, geofence_required=True,
            )
        within, distance_m = check_geofence(
            gps_lat, gps_lon, course.geofence_lat, course.geofence_lon, course.geofence_radius_m)
        if not within:
            return _checkin_response(
                request, token,
                errors=[f"You're outside the check-in area ({distance_m:.0f} m from the classroom). See your instructor."],
                identified_student=student, geofence_required=True,
            )

    # 7. Campus WiFi check — only block when ranges are configured AND the
    #    client is explicitly outside them. Unconfigured (None) means "allow".
    client_ip = request.client.host if request.client else None
    ip_ok = check_ip_range(client_ip, course.allowed_ip_ranges if course else None)
    if ip_ok is False:
        return _checkin_response(request, token, errors=["You must be on campus WiFi to check in."])

    # 8. Late check, using this course's threshold rather than the server's
    status = "present"
    if session.started_at:
        elapsed = (dt.datetime.utcnow() - session.started_at).total_seconds() / 60
        if elapsed > policy_for(course).late_threshold_minutes:
            status = "late"

    # 9. Record attendance
    record = AttendanceRecord(
        session_id=session_id,
        student_id=student.id,
        token_used=token,
        # Raw coordinates are intentionally not persisted: the distance and
        # the in/out flag below are everything the geofence decision needs,
        # and they are what SECURITY.md promises a student. Keeping the
        # coordinates would turn an attendance record into a location history.
        gps_lat=None,
        gps_lon=None,
        gps_distance_m=distance_m,
        ip_address=client_ip,
        ip_in_range=bool(ip_ok) if ip_ok is not None else None,
        status=status,
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError:
        # The duplicate guard above is a SELECT, so two taps that race each
        # other both pass it and the UNIQUE(session_id, student_id)
        # constraint rejects the second. That used to escape as a bare HTTP
        # 500: the student WAS recorded, but was shown "Internal Server
        # Error" and reasonably concluded it had failed. A second tap on a
        # slow phone is the normal case, not an edge case.
        await db.rollback()
        log.info("Duplicate check-in for student %s in session %s, already recorded",
                 student.id, session_id)
        return _checkin_response(
            request, token, success=True, identified_student=student,
            message="You are already checked in for this class. ✅")
    except OperationalError:
        # Almost always "database is locked". The driver already waited, so
        # this is worth telling the student plainly rather than showing a
        # traceback, and worth logging so the instructor can find out why a
        # student says it did not work.
        await db.rollback()
        log.error("Check-in failed for student %s in session %s: database busy",
                  student.id, session_id)
        return _checkin_response(
            request, token,
            errors=["The server was busy and did not record you. Scan the "
                    "code again, and tell your instructor if it keeps "
                    "happening."])

    log.info("Check-in recorded: student %s session %s status %s",
             student.id, session_id, status)
    msg = "Checked in successfully! ✅"
    if status == "late":
        msg = "Checked in (marked late) ⏰"

    return templates.TemplateResponse(request, "student/checkin.html", {
        "token": token,
        "identified_student": student,
        "success": True,
        "message": msg,
    })


def _checkin_response(request, token, errors=None, success=False, message="",
                      identified_student=None, geofence_required=False):
    return templates.TemplateResponse(request, "student/checkin.html", {
        "token": token,
        "identified_student": identified_student,
        "geofence_required": geofence_required,
        "errors": errors or [],
        "success": success,
        "message": message,
    })
