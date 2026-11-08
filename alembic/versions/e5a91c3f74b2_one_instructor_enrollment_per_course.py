"""one instructor enrollment row per course

``course_enrollments`` carried ``UniqueConstraint("course_id", "student_id")``,
which reads as though it constrains the table. It does not constrain
instructor rows at all: an instructor row has ``student_id`` NULL, and in
SQLite every NULL is distinct inside a unique index, so the pair
(course_id, NULL) never collides with itself. Unlimited duplicate instructor
rows were permitted on a single course.

This is the same NULL subtlety that made a ``NOT IN`` orphan sweep report zero
orphans while fourteen sat in the table. It looks like a constraint, it is
named like a constraint, and for half the rows in the table it does nothing.

The consequence was a permanently broken course. ``app/routes/lti.py`` checks
for an existing enrollment with a SELECT and then INSERTs, with no lock, so
two simultaneous launches -- one impatient double-click on a link in D2L --
each saw "not enrolled" and each inserted a row. ``authz.owned_course`` then
read that row with ``scalar_one_or_none``, which raises MultipleResultsFound
on two, so EVERY instructor route for that course answered 500: the course
page, the session page, the exports, the settings. Nothing in the interface
could repair it, because every route that could have was itself 500ing.

The fix is a partial unique index, the same shape as
``uq_sessions_one_open_per_course``: unique on (course_id, instructor_id) over
the rows where instructor_id IS NOT NULL. Student rows, where it is NULL, stay
governed by uq_course_student.

Duplicates are DELETED here rather than preserved, which is the opposite of
how the duplicate-open-session migration treated its rows, and the difference
is deliberate. A duplicate session can hold real attendance records; a
duplicate instructor enrollment holds nothing but itself. It is pure
redundancy: same course, same instructor, same role. The oldest row is kept so
that any ``dropped_at`` history on it survives, and anything that referred to
the discarded ids referred to an identical fact.

Revision ID: e5a91c3f74b2
Revises: a7f3c1d05b84
Create Date: 2026-11-07

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5a91c3f74b2'
down_revision: Union[str, Sequence[str], None] = 'a7f3c1d05b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_enrollments_one_instructor_row_per_course"


def upgrade() -> None:
    conn = op.get_bind()

    # Remove pre-existing duplicates first, or CREATE UNIQUE INDEX fails on
    # the database it is meant to protect. Keep the lowest id per
    # (course_id, instructor_id): it is the original, and it is the one any
    # dropped_at history was written against.
    dupes = conn.execute(sa.text(
        "SELECT id FROM course_enrollments"
        " WHERE instructor_id IS NOT NULL"
        "   AND id NOT IN ("
        "     SELECT MIN(id) FROM course_enrollments"
        "      WHERE instructor_id IS NOT NULL"
        "      GROUP BY course_id, instructor_id"
        "   )"
    )).fetchall()
    if dupes:
        ids = [r[0] for r in dupes]
        print(f"removing {len(ids)} duplicate instructor enrollment(s): {ids}")
        conn.execute(sa.text(
            "DELETE FROM course_enrollments WHERE id IN ("
            + ",".join(str(int(i)) for i in ids) + ")"
        ))

    op.create_index(
        INDEX_NAME, "course_enrollments", ["course_id", "instructor_id"],
        unique=True, sqlite_where=sa.text("instructor_id IS NOT NULL"),
    )


def downgrade() -> None:
    # The removed duplicates are deliberately not recreated. They carried no
    # information, and recreating them would restore the state that made a
    # course permanently unreachable.
    op.drop_index(INDEX_NAME, table_name="course_enrollments")
