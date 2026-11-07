"""Authorization helpers: does THIS instructor own THIS course?

Kept separate from auth.py (which answers "who are you?") because these
answer "what are you allowed to touch?". Both instructor pages and the JSON
API need the same answer, and they must not disagree.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, CourseEnrollment, Instructor, Session, Student, active_roster


async def enrolled_student(db: AsyncSession, course_id: int, student_id: int):
    """Return the Student only if they are on THIS course's roster.

    Owning the session is not enough. Every manual marking route took
    student_id straight from a form, so an instructor could start a session in
    their own course and then post arbitrary ids to it: the attendance API
    read the names and institutional ID numbers back out, which enumerated
    every student on the installation and wrote rows against other
    professors' students. That is the exact guarantee test_isolation.py
    exists to protect, so any route that accepts a student_id must call this.

    A student marked dropped by a classlist import is NOT on the roster for
    this purpose: the routes that call this are the marking routes, and
    recording fresh attendance for somebody who left the course is exactly
    what the drop is meant to stop. Their existing records are untouched and
    still shown on the course page.
    """
    if not isinstance(student_id, int) or student_id <= 0:
        return None
    on_roster = (await db.execute(select(CourseEnrollment.id).where(
        CourseEnrollment.course_id == course_id,
        CourseEnrollment.student_id == student_id,
        *active_roster(),
    ))).scalar_one_or_none()
    return await db.get(Student, student_id) if on_roster else None


async def active_instructor(db: AsyncSession, user: dict | None):
    """The signed-in Instructor, or None if the session is no longer valid.

    A signed cookie alone was enough to keep working after an account was
    deactivated, after an admin reset its password, and after the user
    clicked Logout -- for the cookie's full 12 hours. Checking is_active and
    sessions_valid_from here means revoking access revokes it.
    """
    if not user or user.get("role") != "instructor":
        return None
    instructor = await db.get(Instructor, user.get("instructor_id"))
    if not instructor or not instructor.is_active:
        return None
    issued_at = user.get("_issued_at")
    if (instructor.sessions_valid_from and issued_at
            and issued_at < instructor.sessions_valid_from):
        return None
    return instructor


async def owned_course(db: AsyncSession, user: dict | None, course_id: int):
    """Return the course only if this instructor actually teaches it.

    Authentication is not authorization: every course and session route takes
    an id straight from the URL, so without this check any signed-in
    instructor could read, export, edit or reset another instructor's course
    simply by changing the number -- a student-privacy breach the moment a
    second professor uses the system.
    """
    if not await active_instructor(db, user):
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
    instructor = await active_instructor(db, user)
    if not instructor or not instructor.is_admin:
        return None
    return instructor


async def is_admin(db: AsyncSession, user: dict | None) -> bool:
    """Is this instructor an installation admin?

    Admins own the shared plumbing: the D2L host and the LTI key/secret that
    every course on the install depends on. Ordinary instructors manage their
    own courses only.
    """
    instructor = await active_instructor(db, user)
    return bool(instructor and instructor.is_admin)
