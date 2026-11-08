"""Student-facing routes — check-in, attendance history."""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import devices
from app.auth import get_current_user
from app.clock import utc_now
from app.database import get_db
from app.geo import check_geofence
from app.main import templates
from app.models import (
    AttendanceRecord,
    ClaimConflict,
    Course,
    CourseEnrollment,
    DeviceBinding,
    Institution,
    Session,
    Student,
    active_roster,
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
_FAILURES_MAX_KEYS = 4096

# Claiming a name in a standalone course gets the same treatment, for the
# same reason and with the same two properties: only FAILURES count, and a
# success clears the key. The window is short deliberately -- a student who
# taps the wrong name, or whose token rotated while they were reading the
# list, must not be locked out standing in front of the class.
#
# Note the trap recorded for the typed code: POLLING TO WAIT A LIMIT OUT
# MAKES IT WORSE, because every probe is itself a failed attempt and keeps
# the window topped up. One attempt, then one wait.
_CLAIM_FAILURES: dict[str, list[float]] = {}
# Deliberately generous, for two reasons.
#
# Nothing here is guessable: a student picks their name from a list the page
# already showed them, so grinding attempts buys an attacker nothing. The
# limiter is a deterrent against someone hammering the endpoint, not an access
# control.
#
# And the key is a peer address, which in a classroom is ONE address shared by
# the whole room behind campus NAT. A tight limit there means a handful of
# students tapping the wrong name can lock out everybody else -- far worse
# than the thing being prevented. Set it well above anything an honest room
# produces. Caught when running the test suite twice in a row exhausted the
# old limit of 8 and failed 23 checks that had nothing to do with throttling.
_CLAIM_MAX_FAILURES = 40
_CLAIM_WINDOW_SECONDS = 30


def _attempts_exhausted(bucket: dict[str, list[float]], key: str,
                        window: int, limit: int) -> bool:
    """Has this address already failed too many times recently?"""
    now = time.time()
    recent = [t for t in bucket.get(key, []) if now - t < window]
    if recent:
        bucket[key] = recent
    else:
        bucket.pop(key, None)
    return len(recent) >= limit


def _record_failure(bucket: dict[str, list[float]], key: str) -> None:
    if len(bucket) > _FAILURES_MAX_KEYS:
        bucket.clear()
    bucket.setdefault(key, []).append(time.time())


def _clear_failures(bucket: dict[str, list[float]], key: str) -> None:
    bucket.pop(key, None)


def _code_attempts_exhausted(key: str) -> bool:
    return _attempts_exhausted(_CODE_FAILURES, key, _CODE_WINDOW_SECONDS,
                               _CODE_MAX_FAILURES)


def _record_code_failure(key: str) -> None:
    _record_failure(_CODE_FAILURES, key)


def _clear_code_failures(key: str) -> None:
    _clear_failures(_CODE_FAILURES, key)


def _claim_attempts_exhausted(key: str) -> bool:
    return _attempts_exhausted(_CLAIM_FAILURES, key, _CLAIM_WINDOW_SECONDS,
                               _CLAIM_MAX_FAILURES)


def _record_claim_failure(key: str) -> None:
    _record_failure(_CLAIM_FAILURES, key)


def _clear_claim_failures(key: str) -> None:
    _clear_failures(_CLAIM_FAILURES, key)


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


# ─── Standalone identity: the claim-once device binding ───────
#
# A standalone course has no LMS, so nothing vouches for the student and
# there is no session cookie to read. Identity instead comes from a binding
# this phone made once, and the rules around making that binding ARE the
# security model:
#
#   1. A claim needs a VALID, CURRENT QR token. Claiming is only possible
#      from inside a live check-in, which is what ties a name to being in
#      the room. Without it, anybody on the internet could bind themselves
#      to a student's name.
#   2. One active binding per student per course, enforced by a partial
#      unique index on device_bindings (see app/models.py). A second phone
#      is REFUSED, never silently allowed and never silently moved, and the
#      refusal is recorded for the instructor.
#   3. The cookie is signed with its own itsdangerous salt, scoped to course
#      AND student, and long-lived (see app/devices.py).
#   4. The "who are you?" list shows NAMES ONLY. Student ID numbers and
#      usernames are education records and never appear on it, and only the
#      ACTIVE roster is selectable, so a student the roster reconciliation
#      dropped cannot be claimed.
#
# Standalone has no two-scan problem: there is no external login round trip,
# so the first scan completes the claim AND the check-in in one request.


async def _active_enrollment(db: AsyncSession, course_id: int, student_id: int):
    """This student's enrollment on this course, only if they are on the roster."""
    return (await db.execute(select(CourseEnrollment).where(
        CourseEnrollment.course_id == course_id,
        CourseEnrollment.student_id == student_id,
        *active_roster(),
    ))).scalar_one_or_none()


async def _bound_student(request: Request, db: AsyncSession, course: Course):
    """The student this phone has claimed in this course, or None.

    A correctly signed cookie is necessary and not sufficient. The binding
    row has to still exist and still be active, which is what makes the
    instructor's Release button take effect immediately rather than when the
    cookie happens to expire, and the student has to still be on the active
    roster, so a dropped student cannot keep checking in on an old binding.
    """
    claim = devices.read(request, course.id)
    if not claim:
        return None
    binding = await db.get(DeviceBinding, claim["binding_id"])
    if (binding is None
            or binding.released_at is not None
            or binding.course_id != course.id
            or binding.student_id != claim["student_id"]):
        return None
    if not await _active_enrollment(db, course.id, binding.student_id):
        return None
    return await db.get(Student, binding.student_id)


async def _claimable_students(db: AsyncSession, course_id: int):
    """The names a student may identify themselves as, in roster order.

    Names only. Classmates already know who is in the room, but a student ID
    number or a university username is an education record and publishing a
    list of them to anyone holding a QR token would be a FERPA problem, not
    merely untidy. The form value is the internal row id, which is not an
    institutional identifier and means nothing outside this database.
    """
    return (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            *active_roster(),
        ).order_by(Student.name)
    )).scalars().all()


async def _claim_page(request: Request, db: AsyncSession, course: Course,
                      token: str, error: str = "", status_code: int = 200):
    """The "who are you?" screen."""
    return templates.TemplateResponse(request, "student/claim.html", {
        "course": course,
        "token": token,
        "students": await _claimable_students(db, course.id),
        "error": error,
        # The claim completes the check-in, so it has to collect location
        # when the course enforces a geofence -- the shared checkin.js picks
        # this up from the form's data-geofence attribute.
        "geofence_required": bool(
            course.geofence_enabled
            and course.geofence_lat is not None
            and course.geofence_lon is not None),
    }, status_code=status_code)


# ─── Student check-in ─────────────────────────────────────────

@router.get("/checkin", response_class=HTMLResponse)
async def checkin_page(
    request: Request,
    token: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Page students land on after scanning the QR code.

    Two identity paths, chosen by the course, never by the installation:

    * A D2L course identifies the student from the LTI session cookie. If
      they have not launched the tool yet they are sent to D2L to sign in,
      and a second scan finishes the job.
    * A standalone course identifies them from a device binding this phone
      made earlier. If this phone has no binding, they are asked who they
      are -- and that claim both binds the phone and checks them in, because
      with no external login there is nothing to come back from.
    """
    user = get_current_user(request)
    identified_student = None
    d2l_course_url = None
    geofence_required = False
    resumed_course = None

    valid, session_id, _ = verify_token(token)

    # Standalone courses are resolved first and separately. Identity there
    # comes ONLY from the device binding: an LTI cookie left over from
    # another course must not be allowed to stand in for a claim, or the two
    # identity models would quietly merge.
    if valid:
        session = await db.get(Session, session_id)
        course = await db.get(Course, session.course_id) if session else None
        if (session is not None and session.ended_at is None
                and course is not None and course.is_standalone):
            bound = await _bound_student(request, db, course)
            if bound is None:
                return await _claim_page(request, db, course, token)
            return _checkin_response(
                request, token, identified_student=bound,
                geofence_required=bool(
                    course.geofence_enabled
                    and course.geofence_lat is not None
                    and course.geofence_lon is not None),
            )

    if user and user.get("role") == "student":
        student = await db.get(Student, user["student_id"])
        if student:
            identified_student = student

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
    """Process a student check-in — zero typing, identity from the course's mode."""
    student = None

    # 1. Validate token
    valid, session_id, reason = verify_token(token)
    if not valid:
        return _checkin_response(request, token, errors=[reason])

    # 2. Look up session
    session = await db.get(Session, session_id)
    if not session or session.ended_at is not None:
        return _checkin_response(request, token, errors=["This attendance session has ended."])

    course = await db.get(Course, session.course_id)

    # 3. Identify the student. A standalone course has no LMS to ask, so the
    #    answer is the binding this phone made from inside a live check-in;
    #    a D2L course reads the LTI session cookie, exactly as before.
    if course is not None and course.is_standalone:
        student = await _bound_student(request, db, course)
        if student is None:
            # Unbound phone on a valid token: this is the first scan, so ask
            # who they are rather than refusing. The claim itself will check
            # them in.
            return await _claim_page(request, db, course, token)
    else:
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

    # 4. Check enrollment. A student marked dropped by a classlist import is
    # not enrolled for this purpose; if they have genuinely re-added the
    # course, the LTI launch that brought them here will already have
    # un-dropped them, because D2L granting them access to the course is
    # fresher evidence than a classlist file downloaded last week.
    if not await _active_enrollment(db, session.course_id, student.id):
        return _checkin_response(request, token, errors=["You are not enrolled in this course."])

    return await _record_attendance(request, db, session, course, student,
                                    token, gps_lat, gps_lon)


async def _record_attendance(request: Request, db: AsyncSession, session: Session,
                             course, student, token: str,
                             gps_lat: Optional[float], gps_lon: Optional[float]):
    """Everything after "we know who this is": duplicate, geofence, IP, record.

    Extracted so the standalone claim can complete a check-in in the same
    request without reimplementing any of it. Reimplementing was the real
    risk here: the typed-code fallback was deliberately built to hand off to
    the normal route for exactly this reason, and a second copy of the
    geofence or the duplicate handling would drift.
    """
    session_id = session.id
    distance_m = None

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

    # 7. Late check, using this course's threshold rather than the server's
    status = "present"
    if session.started_at:
        elapsed = (utc_now() - session.started_at).total_seconds() / 60
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


# ─── Claiming a name (standalone courses only) ────────────────


@router.post("/claim", response_class=HTMLResponse)
async def claim_identity(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: str = Form(""),
    student_id: Optional[int] = Form(None),
    gps_lat: Optional[float] = Form(None),
    gps_lon: Optional[float] = Form(None),
):
    """Bind this phone to a name, and check in, in one request.

    Every refusal below is one of the security invariants, so none of them
    may be softened into a warning:

      * no valid token  -> refused. This is what ties identity to being in
        the room; without it the route would let anyone on the internet bind
        themselves to a student's name.
      * course is not standalone -> refused. A D2L course's identity comes
        from the LMS and must not acquire a second, weaker door.
      * name not on the ACTIVE roster -> refused, so a student the roster
        reconciliation dropped cannot be claimed.
      * that student already has an active binding -> refused, recorded, and
        shown to the instructor. Never silently allowed, never silently
        moved to the new phone.
      * this phone already holds a binding in this course -> refused, for
        the same reason: one device checking in two people is proxy
        attendance, which the per-student index alone does not stop.
    """
    client = request.client.host if request.client else "unknown"

    if _claim_attempts_exhausted(client):
        log.warning("claim rate-limited for %s", client)
        return templates.TemplateResponse(request, "student/checkin.html", {
            "token": token,
            "errors": ["Too many tries from this connection. Wait half a "
                       "minute, scan the code again, and tell your "
                       "instructor if it keeps happening."],
        }, status_code=429)

    # INVARIANT 1: a claim is only possible with a token that passes the
    # normal verification.
    valid, session_id, reason = verify_token(token)
    if not valid:
        if session_id is None:
            # Malformed or wrongly signed: nobody reaches this by accident,
            # so it counts against the limiter.
            _record_claim_failure(client)
            log.warning("claim refused for %s: %s", client, reason)
            return templates.TemplateResponse(request, "student/checkin.html", {
                "token": "",
                "errors": ["You are not checked in. Scan the code showing on "
                           "the screen right now."],
            }, status_code=400)
        # Correctly signed but out of date. This is the ordinary case -- the
        # code rotates every 20 seconds and reading a class list takes
        # longer -- so it must not consume a limiter attempt and must not
        # read as an accusation. Offer the list again on a fresh scan.
        session = await db.get(Session, session_id)
        course = await db.get(Course, session.course_id) if session else None
        log.info("claim arrived with an expired token for session %s", session_id)
        if course is not None and course.is_standalone and session.ended_at is None:
            return await _claim_page(
                request, db, course, "",
                error="The code changed while you were choosing. Scan the "
                      "code on screen now and pick your name again.",
                status_code=400)
        return templates.TemplateResponse(request, "student/checkin.html", {
            "token": "",
            "errors": ["You are not checked in. Scan the code showing on "
                       "the screen right now."],
        }, status_code=400)

    session = await db.get(Session, session_id)
    if not session or session.ended_at is not None:
        return _checkin_response(request, token,
                                 errors=["This attendance session has ended."])

    course = await db.get(Course, session.course_id)
    if course is None or not course.is_standalone:
        _record_claim_failure(client)
        log.warning("claim refused on course %s: not a standalone course",
                    session.course_id)
        return _checkin_response(
            request, token,
            errors=["This class identifies students through its learning "
                    "management system. Open Adsum from there first."])

    # One phone, one name, per course.
    already_mine = await _bound_student(request, db, course)
    if already_mine is not None:
        _record_claim_failure(client)
        log.warning("claim refused on course %s: this device is already bound "
                    "to student %s", course.id, already_mine.id)
        if student_id:
            await _record_conflict(db, course.id, int(student_id), session.id, client)
        return await _claim_page(
            request, db, course, token,
            error=f"This phone is already checked in for this class as "
                  f"{already_mine.name}. If that is not you, see your "
                  f"instructor.",
            status_code=409)

    # INVARIANT 4: only a student on the ACTIVE roster is claimable.
    student = None
    if student_id:
        enrollment = await _active_enrollment(db, course.id, int(student_id))
        if enrollment:
            student = await db.get(Student, int(student_id))
    if student is None:
        _record_claim_failure(client)
        log.warning("claim refused on course %s: %r is not on the active roster",
                    course.id, student_id)
        return await _claim_page(
            request, db, course, token,
            error="Choose your name from the list. If it is not there, tell "
                  "your instructor \u2014 you may not be on the class roster yet.",
            status_code=400)

    # INVARIANT 2: one active binding per student per course. The SELECT is a
    # courtesy that produces a good message; the guarantee is the partial
    # unique index, and the IntegrityError below is the race being refused
    # rather than escaping as a 500.
    existing = (await db.execute(select(DeviceBinding).where(
        DeviceBinding.course_id == course.id,
        DeviceBinding.student_id == student.id,
        DeviceBinding.released_at.is_(None),
    ))).scalars().first()
    if existing is None:
        binding = DeviceBinding(course_id=course.id, student_id=student.id,
                                claimed_ip=client)
        db.add(binding)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = (await db.execute(select(DeviceBinding).where(
                DeviceBinding.course_id == course.id,
                DeviceBinding.student_id == student.id,
                DeviceBinding.released_at.is_(None),
            ))).scalars().first()
            log.warning("claim raced on course %s student %s; refusing the "
                        "second device", course.id, student.id)
            binding = None
    else:
        binding = None

    if binding is None:
        _record_claim_failure(client)
        log.warning("CLAIM REFUSED: %s is already claimed on another device "
                    "(course %s, from %s)", student.name, course.id, client)
        await _record_conflict(db, course.id, student.id, session.id, client)
        return await _claim_page(
            request, db, course, token,
            error=f"{student.name} is already checked in on another phone. "
                  f"If that phone is not yours, tell your instructor now \u2014 "
                  f"they can release it and you can try again.",
            status_code=409)

    _clear_claim_failures(client)
    log.info("CLAIM: student %s (%s) bound to a device on course %s from %s "
             "(binding %s)", student.id, student.name, course.id, client,
             binding.id)

    # No two-scan problem here: with no login round trip, the same request
    # that claims the name also records the attendance.
    response = await _record_attendance(request, db, session, course, student,
                                        token, gps_lat, gps_lon)
    devices.issue(response, course.id, student.id, binding.id)
    return response


async def _record_conflict(db: AsyncSession, course_id: int, student_id: int,
                           session_id: Optional[int], client: str) -> None:
    """Remember a refused claim so the instructor can see it.

    A refusal the student alone sees is invisible to the only person who can
    fix it, and the window in which a wrong binding is fixable is the class
    it happened in.
    """
    db.add(ClaimConflict(course_id=course_id, student_id=student_id,
                         session_id=session_id, source_ip=client))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
