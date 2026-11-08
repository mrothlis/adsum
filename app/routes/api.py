"""JSON API endpoints — QR code image, SSE live attendance."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.authz import owned_session
from app.config import settings
from app.database import async_session, get_db
from app.models import AttendanceRecord, CourseEnrollment, Session, active_roster
from app.qr import generate_code, generate_qr_base64, generate_token

router = APIRouter(tags=["api"])


def _local_hms(value) -> str:
    """Format a naive-UTC timestamp as local wall-clock time for the live table."""
    if value is None:
        return ""
    from app.main import _to_local
    return _to_local(value).strftime("%H:%M:%S")


@router.get("/qr/{session_id}")
async def get_qr(request: Request, session_id: int, db: AsyncSession = Depends(get_db)):
    """Return the current QR code as a base64 data-URI plus the check-in URL.

    Instructor-only. This hands out a *valid check-in token*, so leaving it
    open let anyone who guessed a session id check in from outside the room,
    which defeats the point of a rotating in-class QR code.
    """
    user = get_current_user(request)
    # These four conditions were all answered with 404, and the projector page
    # treats 404 as "the session is genuinely over": after three polls it
    # wipes the code off the wall and announces "This session is closed.
    # Students can no longer check in."
    #
    # Only one of the four actually means that. A signed-out instructor (the
    # cookie lasts 12 hours, and signing out in another tab is instant) had
    # the code erased mid-class while students were still scanning, and were
    # told the session had closed when it was wide open. Distinct statuses
    # cost nothing and let the page say something true.
    if not user:
        return JSONResponse(
            {"error": "Not signed in", "reason": "auth"}, status_code=401)

    session = await db.get(Session, session_id)
    if not session:
        return JSONResponse(
            {"error": "No such session", "reason": "missing"}, status_code=404)

    _session, _course = await owned_session(db, user, session_id)
    if not _course:
        return JSONResponse(
            {"error": "Not your session", "reason": "forbidden"},
            status_code=403)

    if session.ended_at is not None:
        # 410 Gone is the honest code for "this existed and is now over", and
        # the page already treats it as final.
        return JSONResponse(
            {"error": "Session ended", "reason": "ended"}, status_code=410)

    token, slot = generate_token(session_id)
    # Build the student check-in URL
    base = settings.BASE_URL.rstrip("/")
    checkin_url = f"{base}/student/checkin?token={token}"
    qr_data_uri = generate_qr_base64(checkin_url)

    # The typed fallback. checkin_url is 133 characters because of the
    # signature, so it was never usable by a student whose camera would not
    # focus; they get a short address to type and a six-character code
    # instead. The code is derived from the same HMAC and the same slot, so
    # it rotates and expires exactly as the QR does.
    return JSONResponse({
        "qr": qr_data_uri,
        "token": token,
        "url": checkin_url,
        "code": generate_code(session_id, slot),
        # The short alias, and without the scheme: a student types this off
        # a projector, so it has to be as close to just the domain as
        # possible. "/c" redirects to the real page.
        "code_url": f"{base.split('://', 1)[-1]}/c",
        "slot": slot,
        "rotation_seconds": settings.QR_ROTATION_SECONDS,
    })


@router.get("/attendance/{session_id}")
async def attendance_list(request: Request, session_id: int, db: AsyncSession = Depends(get_db)):
    """Return current attendance records for a session.

    Instructor-only: this returns student names and ID numbers.
    """
    user = get_current_user(request)
    session, _course = await owned_session(db, user, session_id)
    if not session:
        return JSONResponse({"error": "Not found"}, status_code=404)
    records_stmt = (
        select(AttendanceRecord)
        .options(selectinload(AttendanceRecord.student))
        .where(AttendanceRecord.session_id == session_id)
        .order_by(AttendanceRecord.checked_in_at.desc())
    )
    records = (await db.execute(records_stmt)).scalars().all()

    # The denominator the live page and the projector show. It must exclude
    # students a classlist import found had dropped, or "32 / 40 checked in"
    # counts people who left the course weeks ago.
    enrolled_stmt = (
        select(func.count(CourseEnrollment.id))
        .join(Session, Session.course_id == CourseEnrollment.course_id)
        .where(Session.id == session_id, *active_roster())
    )
    enrolled = (await db.execute(enrolled_stmt)).scalar() or 0

    return JSONResponse({
        "count": len(records),
        "enrolled": enrolled,
        "records": [
            {
                "student_name": r.student.name if r.student else "Unknown",
                "student_id": r.student.student_id_number if r.student else "",
                "d2l_verified": r.student.d2l_verified if r.student else False,
                "status": r.status,
                "checked_in_at": _local_hms(r.checked_in_at),
                "distance_m": r.gps_distance_m,
            }
            for r in records
        ],
    })


@router.get("/attendance/{session_id}/stream")
async def attendance_stream(request: Request, session_id: int):
    """SSE stream of attendance updates for a live session.

    Instructor-only, for the same reason as attendance_list.
    """
    user = get_current_user(request)
    async with async_session() as db:
        session, _course = await owned_session(db, user, session_id)
    if not session:
        return JSONResponse({"error": "Not found"}, status_code=404)

    async def event_generator():
        last_count = -1
        while True:
            if await request.is_disconnected():
                break
            async with async_session() as db:
                session = await db.get(Session, session_id)
                if not session or session.ended_at is not None:
                    yield {"event": "session_ended", "data": json.dumps({"ended": True})}
                    break

                count_stmt = select(func.count(AttendanceRecord.id)).where(
                    AttendanceRecord.session_id == session_id
                )
                count = (await db.execute(count_stmt)).scalar() or 0

                if count != last_count:
                    last_count = count
                    # Fetch latest records
                    rec_stmt = (
                        select(AttendanceRecord)
                        .options(selectinload(AttendanceRecord.student))
                        .where(AttendanceRecord.session_id == session_id)
                        .order_by(AttendanceRecord.checked_in_at.desc())
                    )
                    records = (await db.execute(rec_stmt)).scalars().all()

                    enrolled_stmt = (
                        select(func.count(CourseEnrollment.id))
                        .join(Session, Session.course_id == CourseEnrollment.course_id)
                        .where(Session.id == session_id, *active_roster())
                    )
                    enrolled = (await db.execute(enrolled_stmt)).scalar() or 0

                    data = {
                        "count": count,
                        "enrolled": enrolled,
                        "records": [
                            {
                                "student_name": r.student.name if r.student else "Unknown",
                                "student_id": r.student.student_id_number if r.student else "",
                                "d2l_verified": r.student.d2l_verified if r.student else False,
                                "status": r.status,
                                "checked_in_at": _local_hms(r.checked_in_at),
                            }
                            for r in records
                        ],
                    }
                    yield {"event": "attendance_update", "data": json.dumps(data)}

            await asyncio.sleep(3)

    return EventSourceResponse(event_generator())
