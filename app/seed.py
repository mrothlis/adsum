"""Seed script — create demo data for standalone testing."""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import func, select

from app.auth import hash_password
from app.config import settings
from app.database import async_session
from app.models import Course, CourseEnrollment, Institution, Instructor, Student

log = logging.getLogger(__name__)


async def seed_demo_data() -> None:
    """Insert demo rows only when the DB is empty."""
    async with async_session() as db:
        count = (await db.execute(select(func.count(Instructor.id)))).scalar() or 0
        if count > 0:
            return  # already seeded

        log.info("Seeding demo data …")

        inst = Institution(
            name=settings.INSTITUTION_NAME,
            d2l_host=settings.D2L_HOST or None,
            lti_consumer_key=settings.LTI_CONSUMER_KEY,
            lti_consumer_secret=secrets.token_urlsafe(32),
        )
        db.add(inst)
        await db.flush()

        course = Course(
            institution_id=inst.id,
            name="Example Course 101",
            course_code="EXAMPLE101",
            d2l_org_unit_id="1",
            geofence_lat=settings.DEFAULT_GEOFENCE_LAT,
            geofence_lon=settings.DEFAULT_GEOFENCE_LON,
            geofence_radius_m=settings.DEFAULT_GEOFENCE_RADIUS_M,
        )
        db.add(course)
        await db.flush()

        instructor = Instructor(
            name="Demo Instructor",
            email=settings.ADMIN_EMAIL,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            is_admin=True,
        )
        db.add(instructor)
        await db.flush()

        # Enroll instructor
        db.add(CourseEnrollment(course_id=course.id, instructor_id=instructor.id, role="instructor"))

        # Create 10 demo students
        student_data = [
            ("Alice Johnson", "ajohnson", "900100001"),
            ("Bob Martinez", "bmartinez", "900100002"),
            ("Carla Davis", "cdavis", "900100003"),
            ("David Lee", "dlee", "900100004"),
            ("Emma Wilson", "ewilson", "900100005"),
            ("Frank Thomas", "fthomas", "900100006"),
            ("Grace Kim", "gkim", "900100007"),
            ("Henry Brown", "hbrown", "900100008"),
            ("Iris Patel", "ipatel", "900100009"),
            ("Jack Robinson", "jrobinson", "900100010"),
        ]
        for name, uname, sid in student_data:
            s = Student(
                name=name,
                email=f"{uname}@students.example.edu",
                student_id_number=sid,
            )
            db.add(s)
            await db.flush()
            db.add(CourseEnrollment(course_id=course.id, student_id=s.id, role="student"))

        await db.commit()
        log.info("Demo data seeded: 1 institution, 1 course, 1 instructor, 10 students")


async def ensure_admin() -> None:
    """Production bootstrap: on an empty DB create the institution and one
    admin instructor from ADMIN_EMAIL/ADMIN_PASSWORD. No demo courses/students."""
    async with async_session() as db:
        count = (await db.execute(select(func.count(Instructor.id)))).scalar() or 0
        if count > 0:
            return
        if not settings.ADMIN_PASSWORD or settings.ADMIN_PASSWORD == "admin123":
            log.error("Empty database but ADMIN_PASSWORD is unset/default; "
                      "refusing to create an admin with a known password. Set ADMIN_PASSWORD in .env.")
            return
        inst = Institution(
            name=settings.INSTITUTION_NAME,
            d2l_host=settings.D2L_HOST or None,
            lti_consumer_key=settings.LTI_CONSUMER_KEY,
            lti_consumer_secret=secrets.token_urlsafe(32),
        )
        db.add(inst)
        await db.flush()
        db.add(Instructor(
            name="Administrator",
            email=settings.ADMIN_EMAIL,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            is_admin=True,   # bootstrap account owns the shared D2L/LTI settings
        ))
        await db.commit()
        log.info("Created admin instructor %s", settings.ADMIN_EMAIL)
