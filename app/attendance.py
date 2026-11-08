"""Counting attendance, in one place: the companion to ``app/scoring.py``.

``scoring.py`` owns the arithmetic. This owns the counting that feeds it.

The split existed only in principle before. The counting was written out by
hand in four route bodies, and each one re-derived "present" the same
roundabout way: count the rows whose status is in ``("present", "late")``,
count the ``"late"`` ones separately, then subtract. Three queries per
student, four times over, with the status strings hardcoded as literals in
most copies rather than taken from ``scoring.PRESENT_STATUSES``.

Two problems with that, one slow and one dangerous.

The slow one: ``export_csv`` went further and ran a query per student **per
session**. A 45-student course with 30 class meetings issued 1,350 round
trips to produce one spreadsheet. This app's cost is round trips rather than
SQL -- about 0.5 ms of framework overhead per statement against 0.016 ms of
actual query -- so that is most of a second of pure overhead, and it grows
with the square of the course.

The dangerous one: ``scoring.py``'s own docstring says one implementation of
the arithmetic is what stops two pages disagreeing about a grade. That was
true of the arithmetic and quietly untrue of the counting feeding it. Four
hand-written copies of "count, count again, subtract" is exactly the shape
that stays correct until somebody edits one of them.

Everything here answers in a single grouped query and returns a plain dict,
so a call site loops over students in Python rather than over the database.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttendanceRecord
from app.scoring import (
    EXCUSED_STATUS,
    FULL_CREDIT_STATUSES,
    LATE_STATUS,
    PRESENT_STATUSES,
)


@dataclass(frozen=True)
class Tally:
    """One student's raw counts over a fixed set of sessions.

    ``present`` counts full-credit attendance only, so ``present + late`` is
    the number of times the student was in the room. That is deliberately the
    opposite convention to the queries this replaces, which counted
    present-or-late together and subtracted; naming the fields for what they
    hold removes the subtraction from every call site.
    """

    present: int = 0
    late: int = 0
    excused: int = 0

    @property
    def attended(self) -> int:
        """Bodies in the room, regardless of credit."""
        return self.present + self.late


# A student with no attendance rows at all. Call sites use
# ``tallies.get(student.id, NO_ATTENDANCE)`` so that an empty course and a
# student who never once turned up take the same code path.
NO_ATTENDANCE = Tally()


async def tally_by_student(
    db: AsyncSession, session_ids: list[int]
) -> dict[int, Tally]:
    """Per-student counts over these sessions, in ONE query.

    Students with no records are absent from the result rather than present
    with zeroes: there may be no attendance table rows for them at all, and
    inventing entries would mean passing the roster in just to fill them.
    """
    if not session_ids:
        return {}

    rows = (await db.execute(
        select(AttendanceRecord.student_id, AttendanceRecord.status,
               func.count(AttendanceRecord.id))
        .where(AttendanceRecord.session_id.in_(session_ids))
        .group_by(AttendanceRecord.student_id, AttendanceRecord.status)
    )).all()

    by_status: dict[int, dict[str, int]] = {}
    for student_id, status, count in rows:
        by_status.setdefault(student_id, {})[status] = count

    return {
        student_id: Tally(
            # Derived from FULL_CREDIT_STATUSES rather than hardcoded, so
            # adding a third attending status to scoring.py cannot silently
            # stop counting it here.
            present=sum(n for status, n in statuses.items()
                        if status in FULL_CREDIT_STATUSES),
            late=statuses.get(LATE_STATUS, 0),
            excused=statuses.get(EXCUSED_STATUS, 0),
        )
        for student_id, statuses in by_status.items()
    }


async def status_matrix(
    db: AsyncSession, session_ids: list[int]
) -> dict[int, dict[int, str]]:
    """``{student_id: {session_id: status}}`` for these sessions, in ONE query.

    For the per-session columns in the CSV export and the per-date ledger in
    the D2L export. A missing entry means no record, which is an absence; it
    is not stored as one, because an absence is the lack of a row rather than
    a row saying so.
    """
    if not session_ids:
        return {}

    rows = (await db.execute(
        select(AttendanceRecord.student_id, AttendanceRecord.session_id,
               AttendanceRecord.status)
        .where(AttendanceRecord.session_id.in_(session_ids))
    )).all()

    matrix: dict[int, dict[int, str]] = {}
    for student_id, session_id, status in rows:
        matrix.setdefault(student_id, {})[session_id] = status
    return matrix


async def counts_by_session(
    db: AsyncSession, session_ids: list[int]
) -> dict[int, int]:
    """How many students attended each session, in ONE query.

    Counts attendance in the room sense, present or late, which is what the
    analytics chart plots. Sessions nobody attended are absent from the
    result, so read it with ``.get(session_id, 0)``.
    """
    if not session_ids:
        return {}

    rows = (await db.execute(
        select(AttendanceRecord.session_id, func.count(AttendanceRecord.id))
        .where(AttendanceRecord.session_id.in_(session_ids),
               AttendanceRecord.status.in_(PRESENT_STATUSES))
        .group_by(AttendanceRecord.session_id)
    )).all()
    return {session_id: count for session_id, count in rows}
