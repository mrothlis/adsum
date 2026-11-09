"""Instructor-facing routes — dashboard, sessions, analytics, export."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import time as _time
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import ledger
from app.attendance import (
    NO_ATTENDANCE,
    counts_by_session,
    status_matrix,
    tally_by_student,
)
from app.auth import (
    LTI_LINK_COOKIE,
    LTI_LINK_MAX_AGE,
    clear_session_cookie,
    decode_session_with_issued_at,
    get_current_user,
    set_session_cookie,
    verify_password,
)
from app.authz import (
    active_instructor,
    enrolled_student,
    is_admin,
    owned_course,
    owned_session,
)
from app.clock import utc_now
from app.config import settings
from app.database import get_db
from app.models import (
    AttendanceRecord,
    Course,
    CourseEnrollment,
    Institution,
    Instructor,
    Session,
    Student,
    active_roster,
)
from app.scoring import (
    DEFAULT_ALLOWED_UNEXCUSED,
    DEFAULT_LATE_CREDIT_PERCENT,
    DEFAULT_LATE_THRESHOLD_MINUTES,
    EXCUSED_STATUS,
    LATE_STATUS,
    PRESENT_STATUSES,
    policy_for,
    score,
)
from app.templating import _to_local, templates

log = logging.getLogger("adsum.instructor")
router = APIRouter(prefix="/instructor", tags=["instructor"])

# Upload cap for the roster import. A classlist for even a huge lecture is a
# few tens of kilobytes; anything near this is a mistake (a PowerPoint, a
# photo) and reading it whole into memory first is the wrong thing to do.
MAX_ROSTER_UPLOAD_BYTES = 2 * 1024 * 1024

# ─── Roster reconciliation constants ────────────────────────
#
# Code constants, never settings. Attendance policy is per course and must
# never become an install/.env knob; the same reasoning applies here, except
# more strongly: these are safety limits on a destructive operation, not a
# pedagogical choice anybody should be able to loosen in a config file.

# Refuse a reconciliation that would drop MORE THAN this share of the
# currently-active roster, unless the instructor confirms it deliberately.
#
# Why a half. The realistic accident is not "everyone dropped", it is
# uploading a *filtered* D2L export (one section of a cross-listed course, a
# search result, last term's file, or the grade export instead of the
# classlist). Those files look valid and parse fine, and quietly marking 38
# students dropped would remove them from the gradebook export and from every
# denominator -- a real grade change with no visible cause.
#
# Against that, what does a genuine import look like? Adds and drops in a
# single semester are a handful of students: the university census date
# removes a few percent, and even a brutal first-week shake-out does not take
# half a section away between two classlist downloads. So anything above half
# is far more likely to be the wrong file than the truth, which makes a half
# the point where "a lot of drops" stops being plausible. It is deliberately
# generous rather than tight -- a tiny seminar legitimately loses a large
# fraction, and a guard that cries wolf gets clicked through without reading.
ROSTER_MAX_DROP_FRACTION = 0.5

# How long a roster may go un-imported before the course page and the
# dashboard start saying so. A week is one teaching cycle: long enough that a
# normal instructor is not nagged between two consecutive classes, short
# enough that an add or a drop is caught in the week it happened rather than
# at the end of term when the grade is already wrong.
ROSTER_STALE_AFTER_DAYS = 7

# How many names a summary lists in full before it switches to "and N more".
# The point of the summary is that it is readable; forty names in an alert is
# not read, it is scrolled past.
ROSTER_SUMMARY_NAMES_SHOWN = 8

# The confirmation value the guard requires, following the delete-session
# pattern: enforced server-side, where a mis-sent request cannot slip past a
# JS dialog.
ROSTER_DROP_CONFIRM = "drop"

# Where a classlist CSV actually comes from. Worth stating in one
# place because the obvious guess is wrong: D2L's Classlist tool has
# no export, and the two name checkboxes under Grades -> Export are
# OFF by default, so the natural first attempt produces a file with
# no names in it.
D2L_EXPORT_HELP = (
    'In D2L go to Grades \u2192 Export, set Key Field to Username or Org Defined ID, tick "Last Name" and "First Name" under User Details, then Export to CSV.'
)


def _strip_d2l_key(value: str) -> str:
    """Clean one identifier cell from a D2L export.

    D2L prefixes the key field's values with "#" in the files it produces,
    and which column is the key field is a choice made at export time, so any
    of OrgDefinedId, Username or Email can arrive marked. Strip it everywhere
    rather than guessing.
    """
    return (value or "").strip().lstrip("#").strip()


def _roster_identity_number(rec: dict) -> str:
    """The value that would go in students.student_id_number for this row.

    One definition, used both when matching and when creating, so the lookup
    that prevents a UNIQUE collision cannot drift from the value that would
    cause one.
    """
    return (rec["org_id"] or rec["username"]
            or (rec["email"].split("@")[0] if rec["email"] else ""))


async def _match_roster_student(db: AsyncSession, rec: dict):
    """Find the existing student a classlist row refers to, or None.

    Identifier precedence, most stable first. Username is in here because it
    is the identifier D2L itself relies on: an LTI launch carries
    ext_d2l_username and nothing else about identity, and a gradebook import
    is matched on Username. A real export keyed on Username has no
    OrgDefinedId and no Email column at all, so without the username lookups
    such a file matches nobody and every student is created again as a
    duplicate.

    The student_id_number lookup at the end is the one that stops a 500.
    That column is UNIQUE and is populated from whichever identifier was
    available when the student was first seen, so a row that matched nothing
    above can still collide with it on insert. Found exactly that way: a real
    username-keyed export hit "UNIQUE constraint failed:
    students.student_id_number". Matching is the right answer rather than
    catching the error, because the colliding row IS this student.
    """
    def lower_eq(column, value):
        return select(Student).where(
            func.lower(column) == value.lower()).limit(1)

    if rec["org_id"]:
        student = (await db.execute(
            select(Student).where(
                Student.student_id_number == rec["org_id"]).limit(1)
        )).scalars().first()
        if student:
            return student
    if rec["username"]:
        # Case-insensitive: D2L is not consistent about case between the
        # launch parameter and its own exports.
        student = (await db.execute(
            lower_eq(Student.d2l_username, rec["username"]))).scalars().first()
        if student:
            return student
    if rec["email"]:
        student = (await db.execute(
            lower_eq(Student.email, rec["email"]))).scalars().first()
        if student:
            return student
    number = _roster_identity_number(rec)
    if number:
        student = (await db.execute(
            lower_eq(Student.student_id_number, number))).scalars().first()
        if student:
            return student
    return None


def _graded_sessions_stmt(course_id: int, newest_first: bool = False):
    """Sessions that count towards a grade: the ones that have ENDED.

    A session the instructor has not closed yet is a class still in progress.
    Counting it puts a meeting in the denominator that nobody has had the
    chance to attend, so every student's percentage drops for as long as the
    QR code is on the wall.

    This was a real defect, not a theoretical one. Exporting to the gradebook
    during a live class turned 100/50/50 into 67/50/50 for a three-session
    course, and the at-risk list flagged the whole room mid-lecture. Four
    separate queries had the same omission, which is why there is now one
    definition rather than four.

    The live session page deliberately does NOT use this: it is showing the
    open session, which is the whole point of it.
    """
    order = Session.started_at.desc() if newest_first else Session.started_at
    return (select(Session)
            .where(Session.course_id == course_id,
                   Session.ended_at.is_not(None))
            .order_by(order))


def _roster_freshness(course: Course) -> dict:
    """Plain-language state of this course's last classlist import.

    Worded for a professor, and honest about what the app knows: Adsum cannot
    poll D2L (an LTI 1.1 launch carries no roster, and the Valence
    enrollments API needs an administrator-registered OAuth application), so
    this says when THEY last imported a classlist -- never that D2L was
    checked.

    """
    what = "D2L classlist"
    last = course.roster_last_imported_at
    if last is None:
        # Wording preserved exactly for the D2L case: this sentence is
        # asserted by scripts/test_roster_sync.py, and a rename that changes
        # nothing about behaviour should not break a test.
        return {
            "never": True, "stale": True, "last": None, "days": None,
            "phrase": ("No classlist has been imported into Adsum for this "
                       "course yet."),
        }
    now = utc_now()
    days = max(0, (now - last).days)
    if days == 0:
        phrase = f"You imported a {what} today."
    elif days == 1:
        phrase = f"You imported a {what} yesterday."
    elif days < 14:
        phrase = f"You imported a {what} {days} days ago."
    else:
        weeks = days // 7
        phrase = (f"You imported a {what} about {weeks} weeks ago "
                  f"({_to_local(last).strftime('%b %d')}).")
    return {"never": False, "stale": days >= ROSTER_STALE_AFTER_DAYS,
            "last": last, "days": days, "phrase": phrase}


def _last_import_summary(course: Course) -> Optional[dict]:
    """The stored record of the last classlist import, or None.

    Read back from the course row rather than from the redirect's query
    string, because a query string cannot carry a list of student names and
    the names are the entire point of the alert. A row written by a newer
    version, or corrupted, must not take the course page down.
    """
    raw = course.roster_last_import_summary
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("course %s has an unreadable import summary", course.id)
        return None
    return data if isinstance(data, dict) else None


async def _open_session(db: AsyncSession, course_id: int):
    """The course's open session, or None. Tolerant of duplicates by design.

    There is now a partial unique index making two open sessions impossible,
    but databases created before it may still hold some, and this query must
    never be the thing that takes a page down. ``scalar_one_or_none()`` raised
    MultipleResultsFound on exactly that data, which turned the instructor
    dashboard into a permanent 500 for every instructor of the affected
    course -- an instructor locked out of their own roll call by a stale row.
    An ordered ``limit(1)`` degrades to "show the newest one" instead.
    """
    return (await db.execute(
        select(Session)
        .where(Session.course_id == course_id, Session.ended_at.is_(None))
        .order_by(Session.started_at.desc(), Session.id.desc())
        .limit(1)
    )).scalars().first()



# ─── Auth helpers ───────────────────────────────────────────

def require_instructor(request: Request) -> dict:
    user = get_current_user(request)
    if not user or user.get("role") != "instructor":
        return None
    return user


# ─── Login / Logout ────────────────────────────────────────

# Brute-force throttle, keyed on BOTH the submitted email and the peer
# address, with different thresholds, because the two threats are different.
#
# Per account: a targeted attack guesses one person's password, so five
# failures against one email is already suspicious and locks that account.
#
# Per address: a broad attack sprays many accounts from one place. The
# threshold here is deliberately much higher, because a whole lecture theatre
# can share one campus NAT address and a handful of mistyped passwords must
# not lock the building out. An IP-only counter with a low threshold was
# itself the denial of service.
LOGIN_MAX_FAILS_PER_ACCOUNT = 5
LOGIN_MAX_FAILS_PER_IP = 25
LOGIN_LOCKOUT_SECONDS = 600
# Bounded so a hostile caller cannot grow it without limit.
LOGIN_FAILS_MAX_KEYS = 4096
_login_fails: dict = {}  # key -> [fail_count, lockout_until_epoch]


def _client_ip(request: Request) -> str:
    """The client address, trusting X-Forwarded-For only from a known proxy.

    Reading the first element of X-Forwarded-For unconditionally was a hole,
    not a convenience: the header is client-supplied, and the reverse-proxy
    configuration this project documents APPENDS to it, so the leftmost entry
    is attacker-controlled even behind a correct proxy. The throttle was
    therefore bypassable by incrementing a header, and usable to lock any
    instructor out by replaying their address.

    TRUSTED_PROXY_IPS lists the addresses allowed to speak for someone else.
    Empty (the default) means trust nobody and use the peer address.
    """
    peer = request.client.host if request.client else "?"
    trusted = {h.strip() for h in settings.TRUSTED_PROXY_IPS.split(",") if h.strip()}
    if peer not in trusted:
        return peer
    fwd = request.headers.get("x-forwarded-for", "")
    if not fwd:
        return peer
    # Rightmost entry that is not itself a trusted proxy: entries to the left
    # of that were supplied by the client and cannot be believed.
    for hop in reversed([h.strip() for h in fwd.split(",") if h.strip()]):
        if hop not in trusted:
            return hop
    return peer


def _throttle_keys(request: Request, email: str) -> list[tuple[str, int]]:
    """The counters a failed login increments, each with its own threshold."""
    return [
        (f"user:{(email or '').strip().lower()}", LOGIN_MAX_FAILS_PER_ACCOUNT),
        (f"ip:{_client_ip(request)}", LOGIN_MAX_FAILS_PER_IP),
    ]


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    now = _time.time()
    keys = _throttle_keys(request, email)
    for key, _limit in keys:
        state = _login_fails.get(key)
        if state and state[1] > now:
            log.warning("Login refused: throttled (%s)", key.split(":", 1)[0])
            return HTMLResponse(
                "<h2>Too many login attempts</h2><p>Please try again in 10 "
                "minutes.</p>",
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
        for key, _limit in keys:
            _login_fails.pop(key, None)
        # Straight back to finish a D2L link if a launch offered one, rather
        # than dropping the instructor on the dashboard having apparently
        # ignored what they were in the middle of. The cookie is signed and
        # short-lived; _pending_d2l_link validates it.
        destination = ("/instructor/link-d2l"
                       if _pending_d2l_link(request) else "/instructor/dashboard")
        resp = RedirectResponse(destination, status_code=302)
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

    # Drop the whole table rather than let it grow without bound; losing
    # counters costs an attacker a restart's worth of attempts at most.
    if len(_login_fails) > LOGIN_FAILS_MAX_KEYS:
        _login_fails.clear()
    for key, limit in keys:
        state = _login_fails.get(key) or [0, 0.0]
        state[0] += 1
        if state[0] >= limit:
            state = [0, now + LOGIN_LOCKOUT_SECONDS]
        _login_fails[key] = state
    log.warning("Login failed for %r", email)
    return templates.TemplateResponse(request, "login.html", {
        "error": "Invalid email or password.",
    }, status_code=401)


def _pending_d2l_link(request: Request) -> dict | None:
    """The D2L identity a launch offered to link, or None.

    Read from the short-lived cookie the launch set. Signed with SECRET_KEY
    and age-checked, so the browser cannot invent one: holding this cookie
    proves a correctly signed LTI launch happened recently for that D2L user.
    """
    raw = request.cookies.get(LTI_LINK_COOKIE)
    if not raw:
        return None
    payload, _issued = decode_session_with_issued_at(raw, max_age=LTI_LINK_MAX_AGE)
    if not isinstance(payload, dict) or not payload.get("d2l_user_id"):
        return None
    return payload


@router.get("/link-d2l", response_class=HTMLResponse)
async def link_d2l_page(request: Request):
    """Confirm page for connecting a D2L identity to a password account.

    Reached either straight from the launch's offer page or after signing in,
    because the launch itself cannot see the session cookie: it is a
    cross-site POST and the cookie is SameSite=Lax. This GET is same-site, so
    both the link cookie and the session cookie arrive together, which is the
    whole mechanism.
    """
    pending = _pending_d2l_link(request)
    if not pending:
        return templates.TemplateResponse(request, "lti_link_result.html", {
            "ok": False,
            "heading": "That link request has expired",
            "detail": "Open Adsum from your LMS again to start over. The "
                      "request is only valid for ten minutes.",
        }, status_code=400)
    return templates.TemplateResponse(request, "lti_link.html", {
        "email": pending.get("email"),
        "name": pending.get("name"),
        "institution": None,
        "signed_in": bool(get_current_user(request)),
    })


@router.post("/link-d2l")
async def link_d2l(request: Request, db: AsyncSession = Depends(get_db)):
    """Bind a D2L user id to the signed-in instructor's account.

    Both halves of the identity must be proved in this one request:

    * the link cookie, which is signed and short-lived, proves a correctly
      signed LTI launch happened for that D2L user;
    * the session cookie proves possession of the Adsum password.

    Forging a launch is therefore not enough. An attacker would also need the
    victim's session cookie, at which point they are already signed in as
    them and linking gains them nothing.
    """
    pending = _pending_d2l_link(request)
    if not pending:
        return templates.TemplateResponse(request, "lti_link_result.html", {
            "ok": False,
            "heading": "That link request has expired",
            "detail": "Open Adsum from your LMS again to start over.",
        }, status_code=400)

    user = get_current_user(request)
    if not user or user.get("role") != "instructor":
        # Not signed in yet. The cookie survives, so signing in and coming
        # back finishes the job; login redirects here automatically when it
        # sees the cookie.
        return templates.TemplateResponse(request, "login.html", {
            "error": "Sign in with your Adsum password to finish connecting "
                     "your D2L account.",
            "linking_email": pending.get("email"),
        }, status_code=401)

    instructor = await active_instructor(db, user)
    if not instructor:
        return RedirectResponse("/", status_code=302)

    # The account being linked must be the one the launch named. Without
    # this, anyone who could get a launch offer for address A could sign in
    # as address B and attach A's D2L identity to B's account, which is the
    # account-takeover this whole dance exists to prevent, just reversed.
    launch_email = (pending.get("email") or "").strip().lower()
    if not launch_email or launch_email != (instructor.email or "").strip().lower():
        log.warning("D2L link refused: launch was for %r, signed in as %r",
                    launch_email, instructor.email)
        resp = templates.TemplateResponse(request, "lti_link_result.html", {
            "ok": False,
            "heading": "Those are different accounts",
            "detail": "The launch came from your LMS as "
                      f"{launch_email or 'an unknown address'}, but you are "
                      f"signed in to Adsum as {instructor.email}. Sign in as "
                      "the matching account, or ask an administrator to "
                      "reconcile the two addresses.",
        }, status_code=403)
        resp.delete_cookie(LTI_LINK_COOKIE, path="/")
        return resp

    d2l_user_id = str(pending["d2l_user_id"])

    # Somebody else may already hold this D2L identity. Two accounts sharing
    # one d2l_user_id would make the launch's primary lookup ambiguous, which
    # is the ambiguity the launch resolves FIRST, so it would silently decide
    # who you are.
    holder = (await db.execute(select(Instructor).where(
        Instructor.d2l_user_id == d2l_user_id,
        Instructor.id != instructor.id,
    ).limit(1))).scalars().first()
    if holder:
        log.warning("D2L link refused: %r already holds d2l_user_id", holder.email)
        resp = templates.TemplateResponse(request, "lti_link_result.html", {
            "ok": False,
            "heading": "That D2L account is already connected",
            "detail": "Another Adsum account is already connected to this D2L "
                      "identity. An administrator can disconnect it first.",
        }, status_code=409)
        resp.delete_cookie(LTI_LINK_COOKIE, path="/")
        return resp

    instructor.d2l_user_id = d2l_user_id
    await db.commit()
    log.info("D2L identity linked to instructor %r", instructor.email)

    resp = templates.TemplateResponse(request, "lti_link_result.html", {
        "ok": True,
        "heading": "Connected",
        "detail": "Your D2L account is now connected. Opening Adsum from "
                  "your LMS will sign you in from here on, and your password "
                  "still works as well.",
    })
    resp.delete_cookie(LTI_LINK_COOKIE, path="/")
    return resp


@router.post("/logout")
@router.get("/logout")
async def logout():
    """Clear the session cookie.

    delete_cookie must mirror the attributes set_session_cookie used, or the
    browser keeps the original and logging out appears to work while the
    session stays live.
    """
    resp = RedirectResponse("/", status_code=302)
    clear_session_cookie(resp)
    return resp


# ─── Dashboard ──────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    # The dashboard is the only instructor page that does not go through
    # owned_course, so it needs the revocation check explicitly. Without it a
    # deactivated account, or one whose password had just been reset, still
    # landed here with a working page.
    if not await active_instructor(db, user):
        resp = RedirectResponse("/", status_code=302)
        clear_session_cookie(resp)
        return resp

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
        sess = await _open_session(db, c.id)
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
        # Per-course "when did you last import a classlist", so the card can
        # nag about a stale roster. Adsum cannot ask D2L, so the only thing
        # that reconciles adds and drops is the instructor importing one.
        "roster_status": {c.id: _roster_freshness(c) for c in courses},
    })


# ─── Creating a course ──────────────────────────────────────
#
# Until now a Course could only come into existence in two places --
# app/routes/lti.py and app/seed.py -- so a fresh install's dashboard was
# permanently empty unless somebody launched the tool from D2L. That made
# Adsum unusable for an instructor with no LMS, and it is also what makes the
# app survive D2L retiring LTI 1.1.


@router.get("/courses/new", response_class=HTMLResponse)
async def new_course_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    if not await active_instructor(db, user):
        resp = RedirectResponse("/", status_code=302)
        clear_session_cookie(resp)
        return resp
    return templates.TemplateResponse(request, "instructor/new_course.html", {
        "user": user,
        "error": request.query_params.get("error", ""),
    })


@router.post("/courses/new")
async def create_course(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: Optional[str] = Form(None),
    course_code: Optional[str] = Form(None),
):
    """Create a course owned by the instructor who created it."""
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    instructor = await active_instructor(db, user)
    if not instructor:
        resp = RedirectResponse("/", status_code=302)
        clear_session_cookie(resp)
        return resp

    clean_name = (name or "").strip()
    if not clean_name:
        return RedirectResponse(
            "/instructor/courses/new?error="
            + quote_plus("Give the course a name."), status_code=302)

    # One institution row per install (created by the bootstrap), and it
    # carries the shared D2L host and LTI credentials. Attaching the course
    # to it keeps the launch instructions working.
    institution_id = (await db.execute(
        select(Institution.id).order_by(Institution.id).limit(1))).scalar()

    course = Course(
        institution_id=institution_id,
        name=clean_name,
        course_code=(course_code or "").strip() or None,
        geofence_lat=settings.DEFAULT_GEOFENCE_LAT,
        geofence_lon=settings.DEFAULT_GEOFENCE_LON,
        geofence_radius_m=settings.DEFAULT_GEOFENCE_RADIUS_M,
    )
    db.add(course)
    await db.flush()
    # The creator owns it. owned_course() answers on exactly this row, so
    # without it the instructor would create a course they cannot open.
    db.add(CourseEnrollment(course_id=course.id,
                            instructor_id=instructor.id, role="instructor"))
    await db.commit()
    log.info("course %s (%r) created by instructor %s",
             course.id, clean_name, instructor.id)
    return RedirectResponse(f"/instructor/course/{course.id}", status_code=302)


async def _compute_alerts(db: AsyncSession, course: Course) -> list:
    """Return alert dicts for students with poor attendance."""
    _policy = policy_for(course)
    alerts = []
    # Total sessions for this course
    total_stmt = select(func.count(Session.id)).where(Session.course_id == course.id)
    total_sessions = (await db.execute(total_stmt)).scalar() or 0
    if total_sessions == 0:
        return alerts

    # Enrolled students. Dropped students are excluded: an early alert about
    # somebody who left the course is noise, and their frozen attendance
    # would show as a permanent at-risk warning forever.
    enr_stmt = (
        select(Student)
        .join(CourseEnrollment)
        .where(CourseEnrollment.course_id == course.id, *active_roster())
    )
    students = (await db.execute(enr_stmt)).scalars().all()

    # All sessions ordered by date
    sess_stmt = _graded_sessions_stmt(course.id)
    sessions = (await db.execute(sess_stmt)).scalars().all()
    session_ids = [s.id for s in sessions]

    # Two queries for the whole course. This loop previously ran two per
    # student and loaded full ORM objects to read two columns off them.
    matrix = await status_matrix(db, session_ids)
    tallies = await tally_by_student(db, session_ids)

    for student in students:
        t = tallies.get(student.id, NO_ATTENDANCE)
        statuses = matrix.get(student.id, {})
        # Sessions the student was in the room for, which drives the
        # consecutive-absence streak below. Late counts as being there: a
        # student who arrived late did not miss the class, whatever their
        # credit for it.
        attended_session_ids = {sid for sid, status in statuses.items()
                                if status in PRESENT_STATUSES}
        pct = score(total_sessions, t.present, t.late, t.excused,
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
                "attended": t.attended,
                "total": total_sessions,
                "consecutive_absences": consecutive,
            })
    return alerts


# ─── Course detail ──────────────────────────────────────────

async def _student_stats(db: AsyncSession, students, sessions, _policy) -> list:
    """Per-student attendance figures for a roster table.

    Shared by the active roster and the "Dropped from D2L classlist" section,
    so a dropped student's accumulated attendance is shown with exactly the
    same arithmetic -- that record is what settles a dispute weeks later.
    """
    total_sessions = len(sessions)
    session_ids = [s.id for s in sessions]
    # One query for the whole course, instead of three per student. The
    # queries this replaces counted present-or-late, counted late again, and
    # subtracted; Tally names its fields for what they hold so no call site
    # has to do that arithmetic. See app/attendance.py for why it mattered.
    tallies = await tally_by_student(db, session_ids)
    out = []
    for s in students:
        t = tallies.get(s.id, NO_ATTENDANCE)
        _present, _late, _exc = t.present, t.late, t.excused
        _sc = score(total_sessions, _present, _late, _exc,
                    _policy.allowed_unexcused, _policy.late_credit_percent)
        # Two different percentages, computed here rather than in the
        # template. The roster shows both because the policy-adjusted grade
        # alone was actively misleading: with three absences forgiven, a
        # student who had attended nothing displayed as a green 100% "Good".
        #   raw_pct   - share of classes actually attended, credit-weighted,
        #               which drives the progress bar and the at-risk badge
        #   pct       - the forgiveness-adjusted grade that goes to D2L
        # raw_pct reuses score() with no allowance so that late credit and
        # half-up rounding are applied identically in both columns; computing
        # it in the template with integer division silently floored it and
        # counted a late arrival as a full class.
        _raw = score(total_sessions, _present, _late, _exc, 0,
                     _policy.late_credit_percent)
        out.append({"student": s, "attended": t.attended, "total": total_sessions,
                    "late": _late,
                    "pct": _sc.pct, "raw_pct": _raw.pct,
                    "has_sessions": total_sessions > 0,
                    "excused": _sc.excused,
                    "unexcused": _sc.unexcused, "forgiven": _sc.forgiven,
                    "over_limit": _sc.unexcused_over_limit})
    return out


@router.get("/course/{course_id}", response_class=HTMLResponse)
async def course_detail(
    request: Request,
    course_id: int,
    db: AsyncSession = Depends(get_db),
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

    # The active roster: students still on the D2L classlist as far as the
    # last import could tell.
    enr_stmt = (
        select(Student)
        .join(CourseEnrollment)
        .where(CourseEnrollment.course_id == course_id, *active_roster())
        .order_by(Student.name)
    )
    students = (await db.execute(enr_stmt)).scalars().all()
    student_stats = await _student_stats(db, students, sessions, _policy)

    # Students a classlist import found missing. Kept, never deleted, and
    # shown separately with the date they were detected as gone and the
    # attendance they had accumulated, so a dispute can still be settled.
    dropped_rows = (await db.execute(
        select(Student, CourseEnrollment.dropped_at)
        .join(CourseEnrollment, CourseEnrollment.student_id == Student.id)
        .where(CourseEnrollment.course_id == course_id,
               CourseEnrollment.role == "student",
               CourseEnrollment.dropped_at.is_not(None))
        .order_by(Student.name)
    )).all()
    dropped_stats = await _student_stats(
        db, [row[0] for row in dropped_rows], sessions, _policy)
    for stat, row in zip(dropped_stats, dropped_rows, strict=True):
        stat["dropped_at"] = row[1]

    return templates.TemplateResponse(request, "instructor/course_detail.html", {
        "user": user,
        "course": course,
        "sessions": sessions,
        "student_stats": student_stats,
        "dropped_stats": dropped_stats,
        # The standing record of the last import, read from the course row.
        # It is not a flash: before this it was a transient banner saying
        # "32 new, 4 updated" and the only evidence an import had ever
        # happened.
        "import_result": _last_import_summary(course),
        "roster_status": _roster_freshness(course),
        "stale_after_days": ROSTER_STALE_AFTER_DAYS,
        "summary_names_shown": ROSTER_SUMMARY_NAMES_SHOWN,
        "allowed_absences": _policy.allowed_unexcused,
        "policy": _policy,
    })


@router.post("/course/{course_id}/start-session")
async def start_session(request: Request, course_id: int, db: AsyncSession = Depends(get_db)):
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    if not await owned_course(db, user, course_id):
        return RedirectResponse("/instructor/dashboard", status_code=302)

    # The pre-insert check is a courtesy, not the guarantee: it is a SELECT
    # followed by an INSERT, and a double-clicked button races straight past
    # it. The guarantee is the partial unique index on
    # sessions(course_id) WHERE ended_at IS NULL, so the loser of the race
    # gets an IntegrityError and is sent to the session that actually exists.
    existing = await _open_session(db, course_id)
    if existing:
        return RedirectResponse(f"/instructor/session/{existing.id}", status_code=302)

    session = Session(
        course_id=course_id,
        started_by_instructor_id=user["instructor_id"],
    )
    db.add(session)
    try:
        await db.commit()
    except IntegrityError:
        # Somebody else opened one in the gap. That is the desired end state,
        # not an error: one course, one open session. Join it rather than
        # showing the instructor a 500 at the front of a classroom.
        await db.rollback()
        existing = await _open_session(db, course_id)
        if existing:
            log.info("start-session raced on course %s; joining open session %s",
                     course_id, existing.id)
            return RedirectResponse(f"/instructor/session/{existing.id}",
                                    status_code=302)
        log.warning("start-session on course %s failed and no open session exists",
                    course_id)
        return RedirectResponse(f"/instructor/course/{course_id}", status_code=302)
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
        session.ended_at = utc_now()
        await db.commit()
    return RedirectResponse(f"/instructor/course/{session.course_id}", status_code=302)


@router.post("/course/{course_id}/delete")
async def delete_course(request: Request, course_id: int,
                        db: AsyncSession = Depends(get_db),
                        confirm: str = Form("")):
    """Remove a course and everything belonging to it.

    Needed because a course can now be created in the UI. Before that, every
    course arrived from a D2L launch and a mistyped one was at least rare;
    now a typo in the "New course" form is a dead row an instructor has no
    way to clear, and the only neighbouring tool was reset_course_data, which
    empties a course's attendance but leaves the course itself.

    Deliberately irreversible and deliberately awkward. The confirmation is
    checked server-side, like delete-session, because a dialog is not
    enforcement. Exported attendance can be rebuilt from the D2L gradebook
    with "Restore from D2L"; anything never exported cannot.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Not found", status_code=404)

    if confirm.strip().lower() != "delete":
        log.warning("delete-course refused on course %s: no confirmation",
                    course_id)
        return HTMLResponse(
            "Deleting a course must be confirmed. Nothing was deleted.",
            status_code=400)

    # Count first, for the log and the notice: "deleted a course" is not a
    # useful audit line, "deleted a course with 11 sessions and 240 records"
    # is.
    # Order matters, and foreign keys are enforced (app/database.py sets the
    # PRAGMA), so the child-most rows have to go first: attendance records
    # point at sessions, and sessions point at the course.
    session_ids = list((await db.execute(
        select(Session.id).where(Session.course_id == course_id)
    )).scalars().all())
    record_count = 0
    if session_ids:
        record_count = (await db.execute(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.session_id.in_(session_ids))
        )).scalar() or 0
    if session_ids:
        await db.execute(delete(AttendanceRecord).where(
            AttendanceRecord.session_id.in_(session_ids)))
    await db.execute(delete(Session).where(Session.course_id == course_id))
    await db.execute(delete(CourseEnrollment).where(
        CourseEnrollment.course_id == course_id))

    # Students are shared between courses, so they are not the course's to
    # delete -- except the ones this course was the last to hold. Those rows
    # would otherwise survive forever, invisible, with nothing referring to
    # them: a growing pile of names and ID numbers belonging to a course that
    # no longer exists, which makes it a privacy question rather than a
    # tidiness one. Run AFTER the enrollments are gone so "no remaining
    # enrollment" is true rather than predicted.
    #
    # NOT EXISTS, never NOT IN. This table holds instructor enrollments as
    # well as student ones, and those have student_id NULL by design, so
    # "id NOT IN (SELECT student_id FROM course_enrollments)" is UNKNOWN for
    # every row and matches nothing at all the moment any instructor is
    # enrolled -- which is every real course. It fails by silently doing
    # nothing, so it would not have shown up as an error.
    orphans = (await db.execute(
        select(Student.id).where(~select(CourseEnrollment.id).where(
            CourseEnrollment.student_id == Student.id).exists())
    )).scalars().all()
    if orphans:
        await db.execute(delete(Student).where(Student.id.in_(orphans)))
    # Snapshot what the log and notice need BEFORE the delete: after the
    # commit the ORM object is expired, and touching an attribute then
    # triggers a lazy reload of a row that no longer exists.
    name = course.name
    await db.delete(course)
    await db.commit()

    log.warning("course %s (%r) deleted by instructor %s; "
                "%s session(s) and %s attendance record(s) removed",
                course_id, name, user.get("instructor_id"),
                len(session_ids), record_count)
    notice = (f"Deleted {name}, with {len(session_ids)} "
              f"session{'' if len(session_ids) == 1 else 's'} and "
              f"{record_count} attendance "
              f"record{'' if record_count == 1 else 's'}.")
    return RedirectResponse(f"/instructor/dashboard?notice={quote_plus(notice)}",
                            status_code=302)


@router.post("/session/{session_id}/delete")
async def delete_session(request: Request, session_id: int,
                         db: AsyncSession = Depends(get_db),
                         confirm: str = Form("")):
    """Remove a session entirely, with its attendance records.

    A session started in the hallway, on the wrong course, or twice could be
    ended but never removed, and the exporter treats EVERY session row as a
    class meeting. So a thirty-second stray session gave every student in the
    course an absence and raised the denominator; with three absences
    forgiven by default, a handful of strays quietly spends the allowance
    that real absences were meant to use. The only previous remedy was
    reset_course_data, which destroys the whole course's history.

    Destructive and irreversible, so it needs an explicit confirmation field
    rather than a bare POST -- the same reasoning behind the confirm dialog
    on Reset Session Data, but enforced server-side, where a mis-sent request
    cannot slip past it.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    session, course = await owned_session(db, user, session_id)
    if not session:
        return HTMLResponse("Not found", status_code=404)

    if confirm.strip().lower() != "delete":
        log.warning("delete-session refused on session %s: missing confirmation",
                    session_id)
        return HTMLResponse(
            "Deleting a session must be confirmed. Nothing was deleted.",
            status_code=400)

    course_id = session.course_id
    started_at = session.started_at
    removed = (await db.execute(select(func.count(AttendanceRecord.id)).where(
        AttendanceRecord.session_id == session_id))).scalar() or 0
    await db.execute(delete(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id))
    await db.execute(delete(Session).where(Session.id == session_id))
    await db.commit()
    log.warning("session %s (course %s, started %s) deleted by instructor %s; "
                "%s attendance record(s) removed",
                session_id, course_id, started_at,
                user.get("instructor_id"), removed)
    when = started_at.strftime("%b %d") if started_at else "unknown date"
    notice = (f"Session from {when} deleted, along with "
              f"{removed} attendance record{'' if removed == 1 else 's'}.")
    return RedirectResponse(
        f"/instructor/course/{course_id}?notice={quote_plus(notice)}",
        status_code=302)


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

    # Owning the session is not enough: student_id arrives from a form, so it
    # must be proved to be on THIS course's roster. Without this an
    # instructor could post arbitrary ids into their own session and read
    # other professors' students back out of the attendance API.
    if not await enrolled_student(db, course.id, student_id):
        log.warning("mark-present refused: student %s not on course %s roster",
                    student_id, course.id)
        return HTMLResponse("That student is not on this course's roster.",
                            status_code=404)

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
    # Read-then-insert, so two clicks on the same button race past the
    # existence check and the second hits uq_session_student. The instructor
    # saw a 500 even though the student WAS recorded, which is exactly the
    # confusion the student check-in path was already fixed for.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        log.info("mark-present raced on session %s student %s; record already exists",
                 session_id, student_id)
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
    if not await enrolled_student(db, course.id, student_id):
        log.warning("unmark refused: student %s not on course %s roster",
                    student_id, course.id)
        return HTMLResponse("That student is not on this course's roster.",
                            status_code=404)
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
    if not await enrolled_student(db, course.id, student_id):
        log.warning("%s refused: student %s not on course %s roster",
                    "mark-excused", student_id, course.id)
        return HTMLResponse("That student is not on this course's roster.",
                            status_code=404)
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
    # Same read-then-insert race as mark-present: treat the duplicate as
    # success, because the record existing is the desired end state.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        log.info("mark-excused raced on session %s student %s; record already exists",
                 session_id, student_id)
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

    # Total enrolled. Dropped students are out of the denominator: a
    # "32 / 40 checked in" where 2 of the 40 left the course weeks ago is a
    # wrong number on a projected screen.
    enr_count = (await db.execute(
        select(func.count(CourseEnrollment.id)).where(
            CourseEnrollment.course_id == course.id,
            *active_roster(),
        )
    )).scalar() or 0

    # Students not yet checked in (for manual mark-present). A dropped
    # student must not be offered here -- marking them present would put
    # attendance on somebody who is no longer in the class.
    checked_ids = {r.student_id for r in records}
    enrolled_students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course.id,
            *active_roster(),
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

    sess_stmt = _graded_sessions_stmt(course_id)
    sessions = (await db.execute(sess_stmt)).scalars().all()

    # Per-session attendance counts, one query rather than one per session.
    # A session nobody attended has no rows, so it is missing from the dict
    # and must read as 0 rather than being dropped from the chart: a class
    # with no attendance is exactly the bar worth seeing.
    counts = await counts_by_session(db, [s.id for s in sessions])
    session_labels = [
        _to_local(s.started_at).strftime("%m/%d") if s.started_at else "?"
        for s in sessions
    ]
    session_counts = [counts.get(s.id, 0) for s in sessions]

    # Enrolled count — the active roster only, so the "expected" line on the
    # chart is not inflated by students who dropped.
    enr_count = (await db.execute(
        select(func.count(CourseEnrollment.id)).where(
            CourseEnrollment.course_id == course_id,
            *active_roster(),
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
        _graded_sessions_stmt(course_id)
    )).scalars().all()

    # The active roster only. A dropped student's history stays in the app
    # (course page, "Dropped from D2L classlist"), but this spreadsheet is a
    # grade-facing document and they are not in the class any more.
    students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            *active_roster(),
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
    # Two queries for the whole spreadsheet. This loop used to issue one
    # query per student PER SESSION: a 45-student course with 30 meetings
    # made 1,350 round trips, and the cost grows with the square of the
    # course. A 300-student course took about four seconds, during which an
    # instructor on a classroom machine sees nothing happen.
    matrix = await status_matrix(db, session_ids)
    tallies = await tally_by_student(db, session_ids)

    # The CSV says "Late"/"Excused" rather than the stored status verbatim,
    # so the label is spelled out once here instead of inside the loop.
    labels = {LATE_STATUS: "Late", EXCUSED_STATUS: "Excused"}

    for student in students:
        row = [student.student_id_number, student.name]
        statuses = matrix.get(student.id, {})
        for sid in session_ids:
            status = statuses.get(sid)
            if status is None:
                # No record at all. An absence is the absence of a row, never
                # a row saying "absent".
                row.append("Absent")
            else:
                row.append(labels.get(status, status.capitalize()))

        t = tallies.get(student.id, NO_ATTENDANCE)
        row.append(str(t.attended))
        _sc = score(len(sessions), t.present, t.late, t.excused,
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

    # Existing sessions grouped by local date, as a LIST per date rather than
    # one session per date.
    #
    # Two meetings on one day is ordinary (a lab and a lecture, a Monday
    # double period), and the ledger records them as two tokens carrying the
    # same date. Keying a dict on the date collapsed them: the second token
    # found the first session, the duplicate guard below then skipped it, and
    # the absence won. Measured on a three-session course, a round trip of
    # export then restore turned 100/50/50 into 100/100/75. The information
    # was never lost from the file, only from this dict.
    existing = (await db.execute(
        select(Session).where(Session.course_id == course_id)
        .order_by(Session.started_at))).scalars().all()
    by_date: dict[dt.date, list[Session]] = {}
    for s in existing:
        if s.started_at:
            by_date.setdefault(_to_local(s.started_at).date(), []).append(s)

    # Deliberately NOT filtered to the active roster: a dropped student's
    # ledger in the D2L export is real history, and this is the path that
    # rebuilds it after a data loss. Restoring it changes no grade -- the
    # exports exclude them -- but throwing it away would make the app unable
    # to answer a dispute.
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

        # Reset per row: each student's ledger is a complete record, so the
        # Nth-occurrence counting has to start again for every student.
        seen_for_day: dict[dt.date, int] = {}

        for day, status in entries:
            # The Nth token for a date maps to the Nth session on that date,
            # so repeated dates line up instead of collapsing. The counter is
            # per student row, because each row carries its own full ledger.
            seen_for_day[day] = seen_for_day.get(day, 0) + 1
            nth = seen_for_day[day] - 1
            on_day = by_date.setdefault(day, [])

            while len(on_day) <= nth:
                # Noon local for the first meeting, then an hour later for
                # each additional one, so repeated meetings on a day get
                # distinct and stably ordered timestamps.
                local_noon = dt.datetime(day.year, day.month, day.day, 12, 0)
                local_noon += dt.timedelta(hours=len(on_day))
                offset = settings.DISPLAY_UTC_OFFSET_HOURS
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
                    started = local_noon.replace(tzinfo=tz).astimezone(
                        dt.timezone.utc).replace(tzinfo=None)
                except Exception:
                    started = local_noon - dt.timedelta(hours=offset)
                created = Session(course_id=course_id, started_at=started,
                                  ended_at=started + dt.timedelta(hours=1))
                db.add(created)
                await db.flush()
                on_day.append(created)
                sessions_made += 1

            session = on_day[nth]

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


def _decode_upload(raw: bytes) -> str:
    """Decode an uploaded classlist without ever raising.

    `raw.decode("utf-8-sig")` with no error handler returned HTTP 500 on a
    file holding a single accented name (Jose Garcia with its real accents)
    saved from Excel, which is cp1252 by default on Windows. Nothing was
    imported, the instructor got a bare Internal Server Error with no
    explanation, and the roster stayed empty -- which means every student in
    that class cannot check in at all. The sibling restore-from-d2l endpoint
    already decoded defensively.

    Order matters: utf-8-sig first (correct, and strips the BOM), then cp1252
    (what Excel on Windows produces), and finally utf-8 with replacement so
    that even a PNG or an .xlsx yields a string. Garbage then falls out at
    the header check as a friendly message instead of a traceback.
    """
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@router.post("/course/{course_id}/import-roster")
async def import_d2l_roster(
    request: Request,
    course_id: int,
    db: AsyncSession = Depends(get_db),
    roster_file: UploadFile = File(...),
    # Optional[...] = Form(None), never Form(""): absent means "the
    # instructor has not confirmed anything", which is the safe reading.
    confirm_drops: Optional[str] = Form(None),
):
    """Reconcile the course roster against a D2L Classlist CSV.

    This used to be additive only: it counted new, updated and newly enrolled
    students and never noticed anybody who had disappeared from the
    classlist, so a student who dropped stayed enrolled forever, kept
    accruing absences, and kept appearing in the gradebook export. Now the
    import is a reconciliation -- adds, re-adds, identity changes and drops --
    and it says who, by name.

    Adsum cannot do this on a timer. An LTI 1.1 launch carries no roster, and
    the Valence enrollments API needs an OAuth application registered by a
    Brightspace administrator. So this upload is the only moment the app can
    learn that somebody left, which is why the result is a standing record
    and why a course whose roster has not been imported lately says so.
    """
    user = require_instructor(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    course = await owned_course(db, user, course_id)
    if not course:
        return HTMLResponse("Course not found", status_code=404)

    def bad(message: str):
        """The route's own friendly failure page, with full page context.

        A classlist that cannot be read must say so on the course page, never
        as an Internal Server Error: the instructor's next move is to re-save
        the file, and a 500 does not tell them that.
        """
        log.warning("roster import rejected on course %s: %s", course_id, message)
        _p = policy_for(course)
        return templates.TemplateResponse(request, "instructor/course_detail.html", {
            "user": user, "course": course, "sessions": [], "student_stats": [],
            "dropped_stats": [],
            "import_result": {"error": message},
            "roster_status": _roster_freshness(course),
            "stale_after_days": ROSTER_STALE_AFTER_DAYS,
            "summary_names_shown": ROSTER_SUMMARY_NAMES_SHOWN,
            "allowed_absences": _p.allowed_unexcused, "policy": _p,
            })

    # Cap the upload. Reading one byte past the limit is enough to detect it
    # without pulling an arbitrarily large file into memory.
    raw_bytes = await roster_file.read(MAX_ROSTER_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_ROSTER_UPLOAD_BYTES:
        return bad(f"That file is larger than "
                   f"{MAX_ROSTER_UPLOAD_BYTES // (1024 * 1024)} MB. "
                   f"Upload the D2L Classlist CSV export.")
    if not raw_bytes:
        return bad("That file is empty.")

    text = _decode_upload(raw_bytes)

    # Detect CSV dialect
    try:
        dialect = csv.Sniffer().sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel

    # A non-CSV upload (a PNG, an .xlsx) survives decoding and then dies in
    # the csv module instead -- embedded NULs and over-long fields both
    # raise. Treat any of that as "this is not a classlist", not a 500.
    reader = csv.reader(io.StringIO(text.replace("\x00", "")), dialect)
    try:
        headers = next(reader, None)
        rows = list(reader)
    except csv.Error as exc:
        return bad(f"That file could not be read as a CSV ({exc}). "
                   f"Upload the D2L Classlist CSV export.")
    if not headers:
        return bad("CSV file is empty or has no header row.")

    # Map columns flexibly
    col_first = _match_column(headers, "first name", "first")
    col_last = _match_column(headers, "last name", "last")
    col_orgid = _match_column(headers, "orgdefinedid", "org defined id", "student id")
    col_username = _match_column(headers, "username")
    col_email = _match_column(headers, "email")
    col_role = _match_column(headers, "role")

    if col_first is None and col_last is None:
        return bad("Could not find First Name / Last Name columns in that "
                   "file. D2L leaves those two boxes UNTICKED by default, so "
                   "an export done without them has no names in it. "
                   + D2L_EXPORT_HELP + " Nothing was changed.")

    # ── Phase 1: parse the whole file before touching anything ──
    #
    # The old loop created students, enrolled them and committed as it went.
    # Reconciliation cannot work that way: whether the file is safe to act on
    # at all is only knowable once every row has been read, and the guard
    # below has to be able to change NOTHING.
    parsed: list[dict] = []
    for row in rows:
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

        # D2L marks the KEY FIELD's values with a leading "#" in its own
        # exports, and which column is the key field is chosen at export
        # time. Found with a real export from a live course: with Username as
        # the key field every row read "#<username>", which would have been
        # stored verbatim. That breaks two things at once -- it does not match
        # the existing student, so the import creates a duplicate, and the
        # gradebook export then emits a username D2L does not recognise, so
        # the grade silently lands nowhere. Strip it from every identifier,
        # not just the id column.
        org_id = _strip_d2l_key(row[col_orgid]) if col_orgid is not None and col_orgid < len(row) else ""
        username = _strip_d2l_key(row[col_username]) if col_username is not None and col_username < len(row) else ""
        email = _strip_d2l_key(row[col_email]) if col_email is not None and col_email < len(row) else ""

        if not org_id and not username and not email:
            continue  # nothing here can identify a student

        parsed.append({"name": full_name, "org_id": org_id,
                       "username": username, "email": email})

    if not parsed:
        return bad("No student rows were found in that file. "
                   + D2L_EXPORT_HELP + " Nothing was changed.")

    # ── Phase 2: work out what the file would do ──
    enrolled_rows = (await db.execute(
        select(CourseEnrollment, Student)
        .join(Student, Student.id == CourseEnrollment.student_id)
        .where(CourseEnrollment.course_id == course_id,
               CourseEnrollment.role == "student")
    )).all()
    by_student_id = {s.id: (e, s) for e, s in enrolled_rows}
    active_ids = {s.id for e, s in enrolled_rows if e.dropped_at is None}

    named_ids: set[int] = set()
    for rec in parsed:
        rec["student"] = await _match_roster_student(db, rec)
        student = rec["student"]
        if student is not None and student.id in by_student_id:
            named_ids.add(student.id)

    would_drop_ids = active_ids - named_ids
    overlap = len(active_ids & named_ids)
    confirmed = (confirm_drops or "").strip().lower() == ROSTER_DROP_CONFIRM

    # ── Phase 3: the wrong-file guard ──
    #
    # Importing a filtered export, another section's classlist, or simply the
    # wrong file is the realistic accident, and it must fail loudly rather
    # than quietly mark the whole class dropped. Adds are never blocked by
    # this -- but a refusal applies to the WHOLE import, because an unsafe
    # file is not a file whose adds can be trusted either.
    refusal = None
    if would_drop_ids and not confirmed:
        if active_ids and overlap == 0:
            refusal = "no_overlap"
        elif len(would_drop_ids) > ROSTER_MAX_DROP_FRACTION * len(active_ids):
            refusal = "too_many"

    if refusal:
        # Snapshot everything the page needs as plain Python BEFORE the
        # rollback: rolling back expires every ORM object, and touching one
        # afterwards triggers a lazy reload outside the async greenlet, which
        # is a 500 rather than a refusal page.
        names = sorted(by_student_id[sid][1].name for sid in would_drop_ids)
        course_name = course.name
        course_code = course.course_code
        # Nothing above this point has modified a row, and the rollback makes
        # that a guarantee rather than a reading of the code.
        await db.rollback()
        log.warning("roster import on course %s REFUSED (%s): %s of %s active "
                    "students would be dropped", course_id, refusal,
                    len(would_drop_ids), len(active_ids))
        return templates.TemplateResponse(request, "instructor/import_guard.html", {
            "user": user,
            "course_id": course_id,
            "course_name": course_name,
            "course_code": course_code,
            "reason": refusal,
            "would_drop_names": names,
            "would_drop_count": len(would_drop_ids),
            "active_count": len(active_ids),
            "in_file_count": len(parsed),
            "matched_count": overlap,
            "threshold_percent": int(ROSTER_MAX_DROP_FRACTION * 100),
            "confirm_value": ROSTER_DROP_CONFIRM,
            "summary_names_shown": ROSTER_SUMMARY_NAMES_SHOWN,
        }, status_code=409)

    # ── Phase 4: apply the reconciliation ──
    now = utc_now()
    # The change log goes on screen, so it must not name D2L to an instructor
    # who has no D2L. The columns are still called d2l_* in the schema (they
    # are the same identity fields either way); only the wording changes.
    username_label = "D2L username"
    id_label = "D2L ID"
    added: list[str] = []
    re_added: list[str] = []
    dropped: list[str] = []
    changed: list[dict] = []
    unchanged = 0

    for rec in parsed:
        student = rec["student"]
        if student:
            # Identity drift matters: the gradebook export matches on the D2L
            # username, so a changed one silently stops a grade landing. Say
            # what changed, not just that something did.
            changes = []
            if rec["name"] and student.name != rec["name"]:
                changes.append(f"name: {student.name} \u2192 {rec['name']}")
                student.name = rec["name"]
            if rec["email"] and student.email != rec["email"]:
                changes.append(f"email: {student.email or 'none'} \u2192 {rec['email']}")
                student.email = rec["email"]
            if rec["username"] and student.d2l_username != rec["username"]:
                changes.append(f"{username_label}: "
                               f"{student.d2l_username or 'none'} \u2192 {rec['username']}")
                student.d2l_username = rec["username"]
            if rec["org_id"] and student.d2l_user_id != rec["org_id"]:
                changes.append(f"{id_label}: "
                               f"{student.d2l_user_id or 'none'} \u2192 {rec['org_id']}")
                student.d2l_user_id = rec["org_id"]
            student.d2l_verified = True
            if changes:
                changed.append({"name": student.name, "changes": changes})
            else:
                unchanged += 1
        else:
            student = Student(
                name=rec["name"] or rec["username"] or rec["org_id"],
                email=rec["email"] or None,
                student_id_number=_roster_identity_number(rec),
                d2l_user_id=rec["org_id"] or None,
                d2l_username=rec["username"] or None,
                d2l_verified=True,
            )
            db.add(student)
            await db.flush()  # get student.id

        existing = by_student_id.get(student.id)
        if existing is None:
            db.add(CourseEnrollment(
                course_id=course_id,
                student_id=student.id,
                role="student",
            ))
            added.append(student.name)
        elif existing[0].dropped_at is not None:
            # Back on the classlist. Un-drop rather than create a second
            # enrollment -- uq_course_student would refuse that anyway, and
            # their old attendance is theirs.
            existing[0].dropped_at = None
            re_added.append(student.name)

    for sid in sorted(would_drop_ids):
        enr, st = by_student_id[sid]
        # Marked, never deleted and never unenrolled: the attendance records
        # are real history and the D2L gradebook may still refer to them.
        enr.dropped_at = now
        dropped.append(st.name)

    summary = {
        "at": now.isoformat(timespec="seconds"),
        "in_file": len(parsed),
        "added": sorted(added),
        "re_added": sorted(re_added),
        "dropped": sorted(dropped),
        "changed": changed,
        "unchanged": unchanged,
        "confirmed_drops": bool(confirmed and dropped),
    }
    course.roster_last_imported_at = now
    course.roster_last_import_summary = json.dumps(summary)

    await db.commit()
    log.info("roster import on course %s: %s added, %s re-added, %s dropped, "
             "%s changed, %s unchanged", course_id, len(added), len(re_added),
             len(dropped), len(changed), unchanged)

    # No query parameters: a query string cannot carry a list of names, and
    # the names are the alert. The course page reads the stored summary.
    return RedirectResponse(f"/instructor/course/{course_id}", status_code=302)


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
        _graded_sessions_stmt(course_id)
    )).scalars().all()

    # The active roster only, and this one matters most: D2L rejects a row
    # for a user who is no longer enrolled in the course as an unrecognised
    # user, so a dropped student in this file is at best a confusing error in
    # the import preview. Their record is not lost -- it stays on the course
    # page under "Dropped from D2L classlist".
    students = (await db.execute(
        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id,
            *active_roster(),
        ).order_by(Student.name)
    )).scalars().all()

    total_sessions = len(sessions)
    session_ids = [s.id for s in sessions]

    # Refuse to hand D2L a file that can only destroy the record.
    # If this course has no FINISHED sessions, every student would import as 0
    # and the Attendance Detail ledger would be blanked -- wiping history that
    # D2L is holding. This is the failure mode after a reset or a lost
    # database, so it is blocked outright rather than warned about.
    #
    # The two reasons for having none are told apart deliberately. "No
    # sessions at all" suggests lost data and the remedy is Restore from D2L.
    # "Sessions exist but none have been closed" is the ordinary state during
    # a course's very first class, and telling that instructor their data was
    # lost would be alarming and false.
    if total_sessions == 0:
        any_session = (await db.execute(
            select(func.count(Session.id))
            .where(Session.course_id == course_id))).scalar() or 0
        if any_session:
            return HTMLResponse(
                "<h2>Nothing to export yet</h2>"
                "<p>This course has attendance sessions, but none of them have "
                "been closed yet, so there is nothing final to send to D2L.</p>"
                "<p>A session that is still open is a class in progress: "
                "exporting it would send grades that change the moment you "
                "close it. Use <strong>Stop session</strong> first, then "
                "export.</p>"
                f'<p><a href="/instructor/course/{course_id}">Back to course</a></p>',
                status_code=409)
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

    # Per-session statuses and per-student counts, one query each for the
    # whole course. This loop previously ran three counting queries per
    # student on top of the matrix below, and loaded whole ORM objects to
    # build it when it only ever reads three columns.
    status_by_student = await status_matrix(db, session_ids) if detail else {}
    tallies = await tally_by_student(db, session_ids)

    for student in students:
        t = tallies.get(student.id, NO_ATTENDANCE)
        sc = score(total_sessions, t.present, t.late, t.excused,
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
