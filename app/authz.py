"""Authorization helpers: does THIS instructor own THIS course?

Kept separate from auth.py (which answers "who are you?") because these
answer "what are you allowed to touch?". Both instructor pages and the JSON
API need the same answer, and they must not disagree.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, CourseEnrollment, Instructor, Session


async def owned_course(db: AsyncSession, user: dict | None, course_id: int):
    """Return the course only if this instructor actually teaches it.

    Authentication is not authorization: every course and session route takes
    an id straight from the URL, so without this check any signed-in
    instructor could read, export, edit or reset another instructor's course
    simply by changing the number -- a student-privacy breach the moment a
    second professor uses the system.
    """
    if not user or user.get("role") != "instructor":
        return None
    course = await db.get(Course, course_id)
    if not course:
        return None
    enrolled = (await db.execute(select(CourseEnrollment.id).where(
        CourseEnrollment.course_id == course_id,
        CourseEnrollment.instructor_id == user.get("instructor_id"),
        CourseEnrollment.role == "instructor",
    ))).scalar_one_or_none()
    return course if enrolled else None


async def owned_session(db: AsyncSession, user: dict | None, session_id: int):
    """Return (session, course) only if this instructor teaches that course."""
    if not user:
        return None, None
    session = await db.get(Session, session_id)
    if not session:
        return None, None
    course = await owned_course(db, user, session.course_id)
    if not course:
        return None, None
    return session, course


async def require_admin(db: AsyncSession, user: dict | None):
    """Return the admin Instructor, or None if this user is not an admin.

    Routes use this to answer with 403 rather than silently rendering an
    empty page, so a privilege mistake is loud in testing instead of looking
    like a cosmetic bug.
    """
    if not user or not await is_admin(db, user):
        return None
    return await db.get(Instructor, user.get("instructor_id"))


async def is_admin(db: AsyncSession, user: dict | None) -> bool:
    """Is this instructor an installation admin?

    Admins own the shared plumbing: the D2L host and the LTI key/secret that
    every course on the install depends on. Ordinary instructors manage their
    own courses only.
    """
    if not user or user.get("role") != "instructor":
        return False
    instructor = await db.get(Instructor, user.get("instructor_id"))
    return bool(instructor and instructor.is_admin)
