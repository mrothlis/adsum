"""standalone mode: per-course identity mode, device bindings, claim conflicts

Adds the schema behind standalone mode -- running attendance with no LMS at
all -- and nothing else. Purely additive, and every default is chosen so an
existing install behaves EXACTLY as it did before:

  courses.mode
      NOT NULL, server default 'd2l'. Every existing course keeps the D2L/LTI
      identity path, which is the only path that existed when those rows were
      written, so the live course's behaviour cannot move. 'd2l' is also the
      safe direction: standalone has weaker identity, so no row should ever
      arrive there by a default.

  device_bindings
      One row per (course, student) phone binding, plus the partial unique
      index that is the actual enforcement of "one active binding per student
      per course". The application-level check is a SELECT followed by an
      INSERT, which loses the race between two phones claiming the same name
      at the same moment -- the same defect that produced duplicate open
      sessions, and the same fix: let the database refuse it. The index is
      partial (released_at IS NULL) because released bindings are ordinary
      history and must stay unconstrained, exactly like ended sessions under
      uq_sessions_one_open_per_course.

      SQLite-specific, as that index is. A Postgres port needs
      postgresql_where.

  claim_conflicts
      Refused claims, kept so the instructor sees that one happened. A
      refusal only the student's phone saw is invisible to the one person who
      can fix it, and the window in which a wrong binding is fixable is the
      class it happened in.

The downgrade drops all three. That loses which phone belonged to which
student and the record of refused claims; it does NOT touch a single
attendance record, because attendance belongs to the student and not to the
device. A course that was standalone reverts to the D2L path, which is the
pre-migration behaviour by definition -- standalone could not exist before
this revision.

Revision ID: a7f3c1d05b84
Revises: c3b7f0d41a92
Create Date: 2026-11-07

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7f3c1d05b84'
down_revision: Union[str, Sequence[str], None] = 'c3b7f0d41a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BINDING_INDEX = "uq_device_bindings_one_active_per_student_course"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mode', sa.String(length=20),
                                      nullable=False, server_default='d2l'))

    op.create_table(
        'device_bindings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('course_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(), nullable=False),
        sa.Column('claimed_ip', sa.String(length=50), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('released_by_instructor_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id']),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.ForeignKeyConstraint(['released_by_instructor_id'], ['instructors.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    # THE invariant, expressed where it cannot be raced past.
    op.create_index(
        BINDING_INDEX, 'device_bindings', ['course_id', 'student_id'],
        unique=True, sqlite_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        'claim_conflicts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('course_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('attempted_at', sa.DateTime(), nullable=False),
        sa.Column('source_ip', sa.String(length=50), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id']),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema. No attendance record is touched."""
    op.drop_table('claim_conflicts')
    op.drop_index(BINDING_INDEX, table_name='device_bindings')
    op.drop_table('device_bindings')
    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.drop_column('mode')
