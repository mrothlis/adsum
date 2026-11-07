"""SQLAlchemy ORM models."""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# How a course identifies the student who scans.
#
# "d2l" is the original and only previous behaviour: Brightspace vouches for
# the student through an LTI launch, and the signed session cookie that launch
# issues is the only thing that ever says who scanned. Every course row that
# existed before this column keeps it, so nothing about an LMS-connected
# course changes.
#
# "standalone" is for an instructor with no LMS at all. There is no launch and
# no cookie to read, so identity comes from a claim-once device binding made
# from inside a live check-in. Weaker than an LMS vouching for the student,
# and the README says so plainly rather than selling it.
COURSE_MODE_D2L = "d2l"
COURSE_MODE_STANDALONE = "standalone"
COURSE_MODES = (COURSE_MODE_D2L, COURSE_MODE_STANDALONE)


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
    # Identity path for this course: see COURSE_MODES above. Per course, not
    # per install, because one instructor piloting Adsum without an LMS must
    # not change how a colleague's D2L-connected course identifies students.
    # NOT NULL with a server default of "d2l" so every existing row -- and
    # anything that inserts a Course without naming this column, such as the
    # LTI launch -- keeps the LMS path.
    mode: Mapped[str] = mapped_column(String(20), nullable=False,
                                      default=COURSE_MODE_D2L,
                                      server_default=COURSE_MODE_D2L)
    d2l_org_unit_id: Mapped[Optional[str]] = mapped_column(String(100))
    d2l_grade_object_id: Mapped[Optional[str]] = mapped_column(String(100))
    d2l_attendance_checkin_url: Mapped[Optional[str]] = mapped_column(String(500))
    geofence_lat: Mapped[Optional[float]] = mapped_column(Float)
    geofence_lon: Mapped[Optional[float]] = mapped_column(Float)
    geofence_radius_m: Mapped[float] = mapped_column(Float, default=75.0)
    geofence_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_ip_ranges: Mapped[Optional[str]] = mapped_column(Text)  # comma-separated CIDRs

    # Attendance policy is a syllabus decision, so it belongs to the course,
    # not the server. NULL means "use the institution default from .env",
    # which keeps existing installs behaving exactly as before.
    allowed_unexcused_absences: Mapped[Optional[int]] = mapped_column(Integer)
    late_threshold_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    # Share of a class a late arrival earns, 0-100. NULL = use the default
    # (100). A percentage rather than a yes/no switch because the instructor
    # marks late arrivals by hand and that is the moment they decide how much
    # credit is fair.
    late_credit_percent: Mapped[Optional[int]] = mapped_column(Integer)

    # When a D2L classlist was last imported for this course, and what that
    # import changed. Adsum cannot ask D2L who has added or dropped -- an
    # LTI 1.1 launch carries no roster, and the Valence enrollments API needs
    # an administrator-registered OAuth application -- so reconciliation only
    # happens when the instructor uploads a fresh classlist. These two columns
    # are what lets the app say "your roster may be out of date" honestly, and
    # keep a standing record of the last import instead of a banner that
    # vanishes on the next page load.
    #
    # NULL means "this app has no record of an import", which is the truth for
    # every course that existed before this column, so the migration leaves it
    # NULL rather than inventing a date.
    roster_last_imported_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    # JSON: {"at", "added", "re_added", "dropped", "changed", ...}. Stored
    # rather than passed in the redirect because a query string cannot carry a
    # list of student names, and the names are the whole point of the alert.
    roster_last_import_summary: Mapped[Optional[str]] = mapped_column(Text)

    institution: Mapped[Optional["Institution"]] = relationship(back_populates="courses")
    enrollments: Mapped[List["CourseEnrollment"]] = relationship(back_populates="course", cascade="all, delete-orphan")
    sessions: Mapped[List["Session"]] = relationship(back_populates="course", cascade="all, delete-orphan")

    @property
    def is_standalone(self) -> bool:
        """Does this course run without an LMS?

        A property rather than a template-side string comparison, so the one
        spelling of the mode value lives in this file and a typo in a Jinja
        condition cannot silently show a standalone course the D2L settings.
        """
        return self.mode == COURSE_MODE_STANDALONE


class Instructor(Base):
    __tablename__ = "instructors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    # Institution-wide settings (D2L host, LTI key/secret) are admin-only.
    # A normal instructor manages their own courses and nothing else, so one
    # professor cannot break every other professor's D2L launches.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deactivation rather than deletion: a departed instructor's courses and
    # attendance records must survive, because D2L grade history refers to
    # them. Inactive accounts cannot sign in.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True,
                                            server_default="1", nullable=False)
    # The stable D2L user id, so an LTI launch identifies a person by
    # something that does not change, rather than by a mutable email address.
    d2l_user_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    # True only for accounts an LTI launch created. A launch will never take
    # over a password account, so the shared consumer secret cannot be used to
    # sign in as someone who has a real password.
    lti_provisioned: Mapped[bool] = mapped_column(Boolean, default=False,
                                                  server_default="0", nullable=False)
    # Sessions issued before this moment are refused. Set on a password
    # reset or a deactivation, so revoking access actually revokes it
    # instead of leaving a valid 12-hour cookie in the wild.
    sessions_valid_from: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

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
    # Set when a student was on this course's roster and is missing from a
    # newly imported D2L classlist -- i.e. they dropped the course.
    #
    # MARK, DO NOT DELETE. Their attendance records are real history, a
    # dispute can be raised weeks later, and the D2L gradebook may still hold
    # rows that refer to them. So the row stays and the enrollment stays; this
    # timestamp is what every grade-facing query filters on. A student who
    # reappears in a later import has it set back to NULL and is reported as a
    # re-add. Same reasoning as instructors.is_active: deactivate, never
    # delete.
    dropped_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    course: Mapped["Course"] = relationship(back_populates="enrollments")
    student: Mapped[Optional["Student"]] = relationship(
        back_populates="enrollments", foreign_keys=[student_id],
    )
    instructor: Mapped[Optional["Instructor"]] = relationship(
        back_populates="enrollments", foreign_keys=[instructor_id],
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # A course can hold AT MOST ONE open session, enforced by the
        # database rather than by a SELECT in the route.
        #
        # The route's guard was a SELECT followed by an INSERT with no lock,
        # and four simultaneous POSTs (one impatient double-click is enough)
        # produced four distinct open sessions. Everything downstream then
        # broke at once: the dashboard 500s forever on scalar_one_or_none,
        # the projector shows a QR for one session id while students holding
        # an older code scan into another, and -- worst -- the export counts
        # every session row as a class meeting, so one real class day becomes
        # several and every student who checked into only one of them is
        # exported ABSENT for the rest.
        #
        # SQLite supports partial indexes, so the constraint is expressible
        # exactly: unique on course_id, but only over the open rows. Ended
        # sessions are the normal history and must stay unconstrained.
        Index(
            "uq_sessions_one_open_per_course",
            "course_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

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
    # Raw coordinates are deliberately NOT written here. SECURITY.md and the
    # README both promise that only the distance and an in/out flag are kept,
    # and the code did not honour that: it stored the student's latitude and
    # longitude on every geofenced check-in. The columns remain so existing
    # databases still load, but nothing writes them and the migration clears
    # whatever was captured before.
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


class LtiNonce(Base):
    """One row per accepted LTI launch nonce, kept only for the replay window.

    OAuth 1.0a is only replay-resistant if the verifier remembers nonces.
    Without this table a captured launch body could be posted again inside the
    five-minute timestamp window and would mint a fresh 12-hour session as
    that user. Rows older than the window are deleted on each launch, so the
    table stays small.
    """

    __tablename__ = "lti_nonces"
    __table_args__ = (UniqueConstraint("consumer_key", "nonce", name="uq_lti_nonce"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumer_key: Mapped[str] = mapped_column(String(200), nullable=False)
    nonce: Mapped[str] = mapped_column(String(200), nullable=False)
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow,
                                                 index=True)


class DeviceBinding(Base):
    """One student's phone, bound to one name, in one standalone course.

    This is the whole of student identity in standalone mode, so the rules it
    enforces are the security model rather than a convenience:

    * **A binding can only be created from inside a live check-in**, with a
      QR token that passes the normal verification. The route enforces that;
      without it anybody on the internet could bind themselves to a student's
      name, and identity would no longer be tied to being in the room.
    * **One ACTIVE binding per student per course**, enforced HERE by a
      partial unique index rather than by a SELECT in the route -- the same
      reasoning, and the same shape, as uq_sessions_one_open_per_course. A
      SELECT-then-INSERT loses the race between two phones claiming the same
      name at once, and the loser must be refused, never silently allowed and
      never silently moved onto the new device. The route catches
      IntegrityError and reports the refusal instead of returning 500.
    * **Released, never deleted.** An instructor releasing a binding (new
      phone, wrong name picked) sets released_at, which both frees the name
      for a fresh claim and keeps the history of who held it. Attendance
      records are untouched by a release: they belong to the student, not to
      the device.

    The cookie on the phone is signed with the app's own itsdangerous
    machinery under its own salt (see app/devices.py) and carries this row's
    id, so a release invalidates the cookie immediately -- revocation that
    actually revokes, the same property instructors.sessions_valid_from gives
    the instructor session.
    """

    __tablename__ = "device_bindings"
    __table_args__ = (
        Index(
            "uq_device_bindings_one_active_per_student_course",
            "course_id",
            "student_id",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    claimed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # The address the claim came from, for an instructor investigating a
    # disputed binding. No user agent and no device fingerprint: neither adds
    # anything the instructor can act on, and both would be a new personal
    # record for a tool whose promise is that it keeps as little as possible.
    claimed_ip: Mapped[Optional[str]] = mapped_column(String(50))
    released_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    released_by_instructor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("instructors.id"))

    student: Mapped["Student"] = relationship()

    @property
    def is_active(self) -> bool:
        return self.released_at is None


class ClaimConflict(Base):
    """A refused attempt to claim a student who is already bound.

    Recorded because a refusal that only appears on the student's phone is
    invisible to the person who can fix it. The realistic cases are a student
    who tapped the wrong name on their first scan and a student trying to
    claim a classmate, and both are only fixable while the class is still
    running -- so this is surfaced on the course page, prominently, with the
    Release action next to it.

    Rows are reviewed, not deleted, by the instructor acknowledging them.
    """

    __tablename__ = "claim_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sessions.id"))
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime,
                                                      default=dt.datetime.utcnow)
    source_ip: Mapped[Optional[str]] = mapped_column(String(50))
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    student: Mapped["Student"] = relationship()


def active_roster() -> tuple:
    """WHERE clauses selecting the students currently on a course's roster.

    One definition, used by every grade-facing query, because getting this
    wrong changes real grades: a student who dropped must not appear in the
    D2L gradebook export (D2L rejects the row as an unrecognised user), must
    not sit in any denominator or any "N of M checked in" figure, and must not
    be offered in the mark-present list. Spell the course_id condition at the
    call site and unpack this after it:

        select(Student).join(CourseEnrollment).where(
            CourseEnrollment.course_id == course_id, *active_roster())

    Deliberately NOT applied to the historical paths -- restore-from-d2l has
    to be able to match a dropped student's ledger back, and the course page's
    "Dropped from D2L classlist" section exists precisely to show them.
    """
    return (CourseEnrollment.role == "student",
            CourseEnrollment.dropped_at.is_(None))
