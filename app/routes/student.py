"""Student-facing routes — check-in, attendance history."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
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
from app.qr import verify_token

router = APIRouter(prefix="/student", tags=["student"])


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

    if user and user.get("role") == "student":
        student = await db.get(Student, user["student_id"])
        if student:
            identified_student = student

    # Build the D2L course URL for the "Launch from D2L" button.
    valid, session_id, _ = verify_token(token)
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

    return templates.TemplateResponse(request, "student/checkin.html", {
        "token": token,
        "identified_student": identified_student,
        "d2l_course_url": d2l_course_url,
        "geofence_required": geofence_required,
    })


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

    # 8. Late check
    status = "present"
    if session.started_at:
        elapsed = (dt.datetime.utcnow() - session.started_at).total_seconds() / 60
        if elapsed > settings.LATE_THRESHOLD_MINUTES:
            status = "late"

    # 9. Record attendance
    record = AttendanceRecord(
        session_id=session_id,
        student_id=student.id,
        token_used=token,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        gps_distance_m=distance_m,
        ip_address=client_ip,
        ip_in_range=bool(ip_ok) if ip_ok is not None else None,
        status=status,
    )
    db.add(record)
    await db.commit()

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
