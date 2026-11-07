"""one open session per course

A course could hold several open sessions at once. The guard in
``start_session`` was a SELECT followed by an INSERT with no lock and no
constraint, so simultaneous POSTs -- a double-clicked Start Session button --
each saw "no open session" and each inserted one. Four concurrent requests
reliably produced four distinct open sessions.

The damage is not cosmetic. The instructor dashboard read the open session
with ``scalar_one_or_none`` and so returned 500 forever afterwards; the
projected QR encoded one session id while a student holding a slightly older
code scanned into another; and because the CSV export treats every session
row as a class meeting, one real class day became several meetings, so every
student who checked into only one of them was exported ABSENT for the others.

A SELECT cannot fix that, only the database can. SQLite supports partial
indexes, so the rule is expressible exactly: unique on ``course_id`` over the
rows where ``ended_at IS NULL``. Ended sessions are ordinary history and stay
unconstrained.

This migration carries the data as well as the schema, because an existing
database may already contain duplicates and CREATE UNIQUE INDEX would simply
fail on it. Duplicates are CLOSED, never deleted: those rows may hold real
attendance, and D2L is the system of record. For each course the
most-recently-started open session is kept open (it is the one the instructor
is looking at) and the older ones are stamped ended.

Revision ID: f4c1a7b92d5e
Revises: d90a2bca4b6c
Create Date: 2026-11-06

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4c1a7b92d5e'
down_revision: Union[str, Sequence[str], None] = 'd90a2bca4b6c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_sessions_one_open_per_course"


def upgrade() -> None:
    conn = op.get_bind()

    # Close pre-existing duplicates first, or the index cannot be created.
    # Keep the newest open session per course; stamp the rest ended at their
    # own start time so the history stays chronologically sane and no
    # attendance row is destroyed.
    dupes = conn.execute(sa.text(
        "SELECT id FROM sessions WHERE ended_at IS NULL AND id NOT IN ("
        "  SELECT id FROM sessions s WHERE s.ended_at IS NULL"
        "   AND s.id = ("
        "     SELECT t.id FROM sessions t"
        "      WHERE t.course_id = s.course_id AND t.ended_at IS NULL"
        "      ORDER BY t.started_at DESC, t.id DESC LIMIT 1)"
        ")"
    )).fetchall()
    if dupes:
        ids = [r[0] for r in dupes]
        print(f"closing {len(ids)} duplicate open session(s): {ids}")
        conn.execute(sa.text(
            "UPDATE sessions SET ended_at = COALESCE(started_at, CURRENT_TIMESTAMP) "
            "WHERE id IN (" + ",".join(str(int(i)) for i in ids) + ")"
        ))

    op.create_index(
        INDEX_NAME, "sessions", ["course_id"],
        unique=True, sqlite_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    # The closed duplicates are deliberately not reopened: reopening them
    # would recreate the state this migration exists to make impossible.
    op.drop_index(INDEX_NAME, table_name="sessions")
