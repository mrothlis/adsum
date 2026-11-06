"""LTI 1.1 (Basic LTI) launch endpoint for D2L Brightspace.

Instructors can add this as an External Learning Tool in D2L themselves --
no IT admin registration required. Students launch from D2L, get
auto-authenticated, and land directly in the app.

Validation uses OAuth 1.0a HMAC-SHA1 (stdlib only, no external libraries).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, set_session_cookie
from app.config import settings
from app.database import get_db
from app.models import Course, CourseEnrollment, Institution, Instructor, LtiLaunch, Student

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
    try:
        return abs(time.time() - int(timestamp)) < 300
    except (ValueError, TypeError):
        return False





@router.post("/launch", name="lti_launch")
async def lti_launch(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    params = dict(form)

    consumer_key = params.get("oauth_consumer_key", "")
    inst_stmt = select(Institution).where(Institution.lti_consumer_key == consumer_key)
    institution = (await db.execute(inst_stmt)).scalar_one_or_none()

    if not institution or not institution.lti_consumer_secret:
        return HTMLResponse(
            f"<h2>LTI Error</h2><p>Unknown consumer key: <code>{consumer_key}</code></p>",
            status_code=403)

    oauth_signature = params.pop("oauth_signature", "")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    launch_url = f"{scheme}://{host}{request.url.path}"

    if not _verify_oauth_signature("POST", launch_url, params, institution.lti_consumer_secret, oauth_signature):
        return HTMLResponse(
            "<h2>LTI Signature Error</h2>"
            "<p>Check that the secret in D2L matches Attendance Check-In settings, "
            f"and the URL is exactly: <code>{launch_url}</code></p>",
            status_code=403)

    if not _check_timestamp(params.get("oauth_timestamp", "")):
        return HTMLResponse("<h2>LTI Launch Expired</h2><p>Try again from D2L.</p>", status_code=403)

    user_id = params.get("user_id", "")
    name = params.get("lis_person_name_full", "") or "Unknown"
    email = params.get("lis_person_contact_email_primary", "")
    roles_str = params.get("roles", "")
    context_id = params.get("context_id", "")
    context_title = params.get("context_title", "") or params.get("context_label", "Unknown Course")

    is_instructor = any(r in roles_str for r in ("Instructor", "Administrator", "ContentDeveloper", "TeachingAssistant"))

    course = None
    if context_id:
        course = (await db.execute(select(Course).where(Course.d2l_org_unit_id == context_id))).scalar_one_or_none()
    if not course:
        course = Course(institution_id=institution.id, name=context_title, d2l_org_unit_id=context_id,
                        geofence_lat=settings.DEFAULT_GEOFENCE_LAT, geofence_lon=settings.DEFAULT_GEOFENCE_LON,
                        geofence_radius_m=settings.DEFAULT_GEOFENCE_RADIUS_M)
        db.add(course)
        await db.flush()

    db.add(LtiLaunch(course_id=course.id, user_d2l_id=user_id,
                      role="instructor" if is_instructor else "student",
                      session_data=json.dumps({k: str(v) for k, v in params.items()})))

    if is_instructor:
        instructor = (await db.execute(select(Instructor).where(Instructor.email == email))).scalar_one_or_none()
        if not instructor:
            instructor = Instructor(name=name, email=email or f"lti_{user_id}@d2l.local",
                                    password_hash=hash_password(secrets.token_urlsafe(16)))
            db.add(instructor)
            await db.flush()
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
        await db.commit()
        resp = RedirectResponse("/student/checkin", status_code=302)
        set_session_cookie(resp, {"role": "student", "student_id": student.id, "name": student.name, "lti": True})
        return resp
