"""SQLAlchemy ORM models."""

from __future__ import annotations

import datetime as dt
from typing import Optional, List

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    d2l_host: Mapped[Optional[str]] = mapped_column(String(500))
    # LTI 1.1 (Basic LTI) — instructor-configurable, no IT admin needed
    lti_consumer_key: Mapped[Optional[str]] = mapped_column(String(500))
    lti_consumer_secret: Mapped[Optional[str]] = mapped_column(String(500))

    courses: Mapped[List["Course"]] = relationship(back_populates="institution")


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    institution_id: Mapped[Optional[int]] = mapped_column(ForeignKey("institutions.id"))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    course_code: Mapped[Optional[str]] = mapped_column(String(50))
    d2l_org_unit_id: Mapped[Optional[str]] = mapped_column(String(100))
    d2l_grade_object_id: Mapped[Optional[str]] = mapped_column(String(100))
    d2l_attendance_checkin_url: Mapped[Optional[str]] = mapped_column(String(500))
    geofence_lat: Mapped[Optional[float]] = mapped_column(Float)
    geofence_lon: Mapped[Optional[float]] = mapped_column(Float)
    geofence_radius_m: Mapped[float] = mapped_column(Float, default=75.0)
    geofence_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_ip_ranges: Mapped[Optional[str]] = mapped_column(Text)  # comma-separated CIDRs

    institution: Mapped[Optional["Institution"]] = relationship(back_populates="courses")
    enrollments: Mapped[List["CourseEnrollment"]] = relationship(back_populates="course", cascade="all, delete-orphan")
    sessions: Mapped[List["Session"]] = relationship(back_populates="course", cascade="all, delete-orphan")


class Instructor(Base):
    __tablename__ = "instructors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)

    enrollments: Mapped[List["CourseEnrollment"]] = relationship(
        back_populates="instructor",
        foreign_keys="[CourseEnrollment.instructor_id]",
    )


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(300))
    student_id_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    d2l_user_id: Mapped[Optional[str]] = mapped_column(String(100))
    d2l_username: Mapped[Optional[str]] = mapped_column(String(200))
    d2l_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    enrollments: Mapped[List["CourseEnrollment"]] = relationship(back_populates="student")
    attendance_records: Mapped[List["AttendanceRecord"]] = relationship(back_populates="student")


class CourseEnrollment(Base):
    __tablename__ = "course_enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "student_id", name="uq_course_student"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id"))
    instructor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instructors.id"))
    role: Mapped[str] = mapped_column(String(20), default="student")  # 'instructor' | 'student'

    course: Mapped["Course"] = relationship(back_populates="enrollments")
    student: Mapped[Optional["Student"]] = relationship(
        back_populates="enrollments", foreign_keys=[student_id],
    )
    instructor: Mapped[Optional["Instructor"]] = relationship(
        back_populates="enrollments", foreign_keys=[instructor_id],
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=func.now())
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    started_by_instructor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instructors.id"))
    # D2L course URL for "Launch from D2L" button on check-in page
    d2l_course_url: Mapped[Optional[str]] = mapped_column(String(500))

    course: Mapped["Course"] = relationship(back_populates="sessions")
    instructor: Mapped[Optional["Instructor"]] = relationship()
    attendance_records: Mapped[List["AttendanceRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")

    @property
    def is_active(self) -> bool:
        return self.ended_at is None


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_session_student"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    checked_in_at: Mapped[dt.datetime] = mapped_column(DateTime, default=func.now())
    token_used: Mapped[Optional[str]] = mapped_column(String(500))
    gps_lat: Mapped[Optional[float]] = mapped_column(Float)
    gps_lon: Mapped[Optional[float]] = mapped_column(Float)
    gps_distance_m: Mapped[Optional[float]] = mapped_column(Float)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50))
    ip_in_range: Mapped[Optional[bool]] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), default="present")  # present | late | invalid

    session: Mapped["Session"] = relationship(back_populates="attendance_records")
    student: Mapped["Student"] = relationship(back_populates="attendance_records")


class LtiLaunch(Base):
    __tablename__ = "lti_launches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id"))
    user_d2l_id: Mapped[Optional[str]] = mapped_column(String(100))
    role: Mapped[Optional[str]] = mapped_column(String(50))
    launched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=func.now())
    session_data: Mapped[Optional[str]] = mapped_column(Text)
