"""LTI 1.1 (Basic LTI) launch endpoint for D2L Brightspace.

Instructors can add this as an External Learning Tool in D2L themselves --
no IT admin registration required. Students launch from D2L, get
auto-authenticated, and land directly in the app.

Validation uses OAuth 1.0a HMAC-SHA1 (stdlib only, no external libraries).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import escape
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, set_session_cookie
from app.config import settings
from app.database import get_db
from app.models import (
    Course,
    CourseEnrollment,
    Institution,
    Instructor,
    LtiLaunch,
    LtiNonce,
    Student,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["lti"])


def _percent_encode(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _verify_oauth_signature(method, url, params, consumer_secret, oauth_signature):
    sorted_params = sorted(params.items())
    param_string = "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_params)
    base_string = "&".join([method.upper(), _percent_encode(url), _percent_encode(param_string)])
    signing_key = f"{_percent_encode(consumer_secret)}&"
    hashed = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1)
    expected = base64.b64encode(hashed.digest()).decode()
    return hmac.compare_digest(expected, oauth_signature)


def _check_timestamp(timestamp):
    """Accept a launch from the recent past only.

    The window is asymmetric on purpose: a little tolerance for clock skew
    behind us, almost none ahead, so a launch minted with a future timestamp
    cannot buy itself a longer replay window.
    """
    try:
        skew = time.time() - int(timestamp)
    except (ValueError, TypeError):
        return False
    return -30 < skew < 300


def _parse_roles(roles_str: str) -> bool:
    """Decide whether this launch is a COURSE instructor.

    Substring matching was wrong and dangerous. A Brightspace launch sends a
    comma-separated list of full role URNs, and institution-level roles appear
    alongside course-level ones. Someone who is staff at the institution but
    merely a Learner in this particular course arrives with a roles string
    containing "Instructor", so `"Instructor" in roles_str` handed them
    instructor access to a course they are taking. Institution-level roles
    live under the instrole namespace, so they are discarded and only the
    context (course) roles are considered.
    """
    course_roles = []
    for raw in roles_str.split(","):
        role = raw.strip()
        if not role:
            continue
        # urn:lti:instrole:... is an institution role, not a course role.
        if "instrole" in role.lower():
            continue
        course_roles.append(role.rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower())
    return any(r in ("instructor", "contentdeveloper", "teachingassistant",
                     "administrator", "mentor")
               for r in course_roles)





@router.post("/launch", name="lti_launch")
async def lti_launch(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    params = dict(form)

    consumer_key = params.get("oauth_consumer_key", "")
    inst_stmt = select(Institution).where(Institution.lti_consumer_key == consumer_key)
    institution = (await db.execute(inst_stmt)).scalar_one_or_none()

    if not institution or not institution.lti_consumer_secret:
        # The key is NOT echoed back. It used to be interpolated into an
        # f-string, which made this unauthenticated endpoint a reflected XSS
        # sink on the app's own origin -- and from there an attacker's script
        # could read the shared LTI secret out of /admin. The value goes to
        # the log instead, where it is useful and harmless.
        log.warning("LTI launch rejected: unknown consumer key %r", consumer_key)
        return HTMLResponse(
            "<h2>LTI Error</h2><p>This tool link is not registered with "
            "Adsum. Ask your administrator to check the consumer key.</p>",
            status_code=403)

    oauth_signature = params.pop("oauth_signature", "")
    # Built from BASE_URL, never from X-Forwarded-Host / X-Forwarded-Proto.
    # Those are client-supplied, so an attacker could both steer the
    # signature base string and inject markup into the error page below.
    launch_url = settings.BASE_URL.rstrip("/") + request.url.path

    if not _verify_oauth_signature("POST", launch_url, params, institution.lti_consumer_secret, oauth_signature):
        log.warning("LTI launch rejected: bad OAuth signature for key %r", consumer_key)
        return HTMLResponse(
            "<h2>LTI Signature Error</h2>"
            "<p>Check that the secret in D2L matches the one in Adsum, "
            f"and that the launch URL is exactly: <code>{escape(launch_url)}</code></p>",
            status_code=403)

    if not _check_timestamp(params.get("oauth_timestamp", "")):
        log.warning("LTI launch rejected: stale or future timestamp")
        return HTMLResponse("<h2>LTI Launch Expired</h2><p>Open the link again "
                            "from D2L.</p>", status_code=403)

    # Replay protection. OAuth 1.0a requires a nonce store and there was
    # none, so a captured launch body could be posted again within the
    # timestamp window to mint a fresh 12-hour session as that user. Launch
    # bodies are recoverable from a shared classroom machine, a proxy log, or
    # this app's own lti_launches table.
    nonce = str(params.get("oauth_nonce", "")).strip()
    if not nonce:
        log.warning("LTI launch rejected: missing oauth_nonce")
        return HTMLResponse("<h2>LTI Error</h2><p>Launch was missing its "
                            "nonce.</p>", status_code=403)
    db.add(LtiNonce(consumer_key=consumer_key, nonce=nonce))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        log.warning("LTI launch rejected: replayed nonce for key %r", consumer_key)
        return HTMLResponse("<h2>Link Already Used</h2><p>Open the Adsum link "
                            "again from inside D2L.</p>", status_code=403)
    # Only the window matters, so old rows are swept rather than kept.
    await db.execute(delete(LtiNonce).where(
        LtiNonce.seen_at < dt.datetime.utcnow() - dt.timedelta(minutes=15)))

    if params.get("lti_message_type") not in (None, "", "basic-lti-launch-request"):
        log.warning("LTI launch rejected: unexpected message type %r",
                    params.get("lti_message_type"))
        return HTMLResponse("<h2>LTI Error</h2><p>Unsupported launch "
                            "type.</p>", status_code=403)

    user_id = params.get("user_id", "")
    name = params.get("lis_person_name_full", "") or "Unknown"
    email = params.get("lis_person_contact_email_primary", "")
    roles_str = params.get("roles", "")
    context_id = params.get("context_id", "")
    context_title = params.get("context_title", "") or params.get("context_label", "Unknown Course")

    is_instructor = _parse_roles(roles_str)

    course = None
    if context_id:
        # Scoped to the institution: a context_id colliding with another
        # institution's course must not join the two together.
        course = (await db.execute(select(Course).where(
            Course.d2l_org_unit_id == context_id,
            Course.institution_id == institution.id,
        ))).scalar_one_or_none()
    if not course:
        course = Course(institution_id=institution.id, name=context_title, d2l_org_unit_id=context_id,
                        geofence_lat=settings.DEFAULT_GEOFENCE_LAT, geofence_lon=settings.DEFAULT_GEOFENCE_LON,
                        geofence_radius_m=settings.DEFAULT_GEOFENCE_RADIUS_M)
        db.add(course)
        await db.flush()

    # Only the fields that are actually used later are kept. The whole
    # parameter dump used to be stored forever, which meant every launch left
    # a permanent copy of a student's name and email in the database, and a
    # replayable launch body alongside it.
    db.add(LtiLaunch(course_id=course.id, user_d2l_id=user_id,
                      role="instructor" if is_instructor else "student",
                      session_data=json.dumps({
                          "context_id": context_id,
                          "context_title": context_title,
                          "roles": roles_str,
                          "lti_version": str(params.get("lti_version", "")),
                      })))

    if is_instructor:
        # Identity comes from the stable D2L user id first, and only then
        # from the email address. Matching on email alone let a launch bind
        # to ANY existing account, so a correctly signed launch carrying the
        # admin's email address logged straight in as the admin with no
        # password -- turning the single shared consumer secret into a master
        # key and bypassing the login throttle entirely.
        instructor = (await db.execute(select(Instructor).where(
            Instructor.d2l_user_id == user_id))).scalar_one_or_none()
        if not instructor and email:
            candidate = (await db.execute(select(Instructor).where(
                Instructor.email == email))).scalar_one_or_none()
            # Adopt an existing account only if it was created by LTI in the
            # first place. A password account is never taken over by a launch.
            if candidate and candidate.lti_provisioned:
                instructor = candidate
                instructor.d2l_user_id = user_id
            elif candidate:
                log.warning("LTI launch refused to adopt password account %r", email)
                return HTMLResponse(
                    "<h2>Account Already Exists</h2>"
                    "<p>There is already an Adsum account for your email "
                    "address. Sign in with your password instead.</p>",
                    status_code=403)
        if not instructor:
            instructor = Instructor(name=name, email=email or f"lti_{user_id}@d2l.local",
                                    password_hash=hash_password(secrets.token_urlsafe(32)),
                                    d2l_user_id=user_id, lti_provisioned=True)
            db.add(instructor)
            await db.flush()
        # An admin must prove possession of a password. Admin rights reach
        # account management and the shared D2L credentials, which is too much
        # to grant on the strength of one install-wide shared secret.
        if instructor.is_admin:
            log.warning("LTI launch refused for admin account %r", instructor.email)
            return HTMLResponse(
                "<h2>Password Required</h2><p>Administrator accounts must "
                "sign in with a password rather than through D2L.</p>",
                status_code=403)
        if not instructor.is_active:
            log.warning("LTI launch refused for deactivated account %r", instructor.email)
            return HTMLResponse(
                "<h2>Account Deactivated</h2><p>Ask your administrator to "
                "reactivate your Adsum account.</p>", status_code=403)
        enr = (await db.execute(select(CourseEnrollment).where(
            CourseEnrollment.course_id == course.id, CourseEnrollment.instructor_id == instructor.id))).scalar_one_or_none()
        if not enr:
            db.add(CourseEnrollment(course_id=course.id, instructor_id=instructor.id, role="instructor"))
        await db.commit()
        resp = RedirectResponse(f"/instructor/course/{course.id}", status_code=302)
        set_session_cookie(resp, {"role": "instructor", "instructor_id": instructor.id, "name": instructor.name, "lti": True})
        return resp
    else:
        student = (await db.execute(select(Student).where(Student.d2l_user_id == user_id))).scalar_one_or_none()
        if not student and email:
            student = (await db.execute(select(Student).where(Student.email == email))).scalar_one_or_none()
        if not student:
            student = Student(name=name, email=email, student_id_number=user_id, d2l_user_id=user_id)
            db.add(student)
            await db.flush()
        elif not student.d2l_user_id:
            student.d2l_user_id = user_id
        enr = (await db.execute(select(CourseEnrollment).where(
            CourseEnrollment.course_id == course.id, CourseEnrollment.student_id == student.id))).scalar_one_or_none()
        if not enr:
            db.add(CourseEnrollment(course_id=course.id, student_id=student.id, role="student"))
        elif enr.dropped_at is not None:
            # A previous classlist import marked them dropped, and D2L has
            # just launched them into this course anyway -- which means D2L
            # currently grants them access. That is fresher evidence than the
            # file, so un-drop them rather than leave a re-added student
            # unable to check in until the instructor remembers to re-import.
            log.info("LTI launch un-dropped student %s on course %s",
                     student.id, course.id)
            enr.dropped_at = None
        await db.commit()
        resp = RedirectResponse("/student/checkin", status_code=302)
        set_session_cookie(resp, {"role": "student", "student_id": student.id, "name": student.name, "lti": True})
        return resp
