"""Remove standalone mode: drop device_bindings, claim_conflicts, courses.mode

Revision ID: c3f7a2b54e18
Revises: b8c4e1f29a07
Create Date: 2026-11-09

Adsum now identifies a student in exactly one way: D2L vouches for them
through an LTI launch. The second path, "standalone mode", is gone.

In standalone mode a course needed no LMS. A student scanned, picked their
own name from the roster, and that phone was bound to that name for the
term. It was the weaker path and the interface said so, because at that
first scan a student could pick a classmate's name. The mitigations were
real but all after the fact: one active binding per student per course, the
refused second attempt recorded and shown to the instructor, and a release
button. None of them stop the first wrong claim; they only make it visible
afterwards.

With one identity path instead of two, there is no weaker door to reason
about, no mode to choose and nothing to explain about the trade.

What this drops:

  device_bindings   which phone is which student. The whole of standalone
                    identity.
  claim_conflicts   refused claims awaiting an instructor's review.
  courses.mode      "d2l" or "standalone". With one path it is a column
                    that can only hold one value.

Attendance, rosters, sessions, grades and the geofence are untouched: none
of them ever consulted the mode. A course that was running in standalone
mode keeps every record it had, and its students stay on the roster. What
changes is that they can no longer check in until they open Adsum from D2L,
because that is now the only thing that can say who they are.

The downgrade recreates both tables and the column, empty. Bindings
themselves cannot be restored, because a binding is a claim a specific
phone made and nothing else records it. That is why this file is a one-way
door in practice even though the downgrade runs cleanly.
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f7a2b54e18"
down_revision = "b8c4e1f29a07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Child-most first. claim_conflicts references sessions, and both tables
    # reference courses and students, so dropping them before touching
    # anything else keeps foreign keys satisfied at every step.
    op.drop_index("uq_device_bindings_one_active_per_student_course",
                  table_name="device_bindings")
    op.drop_table("claim_conflicts")
    op.drop_table("device_bindings")

    # SQLite cannot DROP COLUMN in place before 3.35 and Alembic's batch mode
    # rebuilds the table, which is the portable route either way.
    with op.batch_alter_table("courses") as batch:
        batch.drop_column("mode")


def downgrade() -> None:
    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("mode", sa.String(length=20),
                                   nullable=False, server_default="d2l"))

    op.create_table(
        "device_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_ip", sa.String(length=50), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("released_by_instructor_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["released_by_instructor_id"],
                                ["instructors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index: one ACTIVE binding per student per course, while
    # released rows stay as history. A plain unique index would have made a
    # release followed by a re-claim impossible.
    op.create_index("uq_device_bindings_one_active_per_student_course",
                    "device_bindings", ["course_id", "student_id"],
                    unique=True, sqlite_where=sa.text("released_at IS NULL"))

    op.create_table(
        "claim_conflicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("source_ip", sa.String(length=50), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
