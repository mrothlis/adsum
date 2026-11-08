"""roster reconciliation: dropped_at and last-import record

Revision ID: c3b7f0d41a92
Revises: f4c1a7b92d5e
Create Date: 2026-11-07 15:10:00.000000

Purely additive, and the data it carries is "nothing happened yet", which is
the honest translation in both directions:

  course_enrollments.dropped_at
      NULL means "still on the classlist". Every row that exists before this
      migration IS still on the classlist as far as the app knows, because
      the import was additive only and never noticed a drop. So NULL is the
      correct value for existing rows rather than a default that would
      silently drop somebody.

  courses.roster_last_imported_at / roster_last_import_summary
      NULL means "this app has no record of a classlist import". Back-filling
      a date here would make the stale-roster warning lie about something the
      app never observed, so existing courses stay NULL and are shown as
      "never imported" until the instructor imports one.

The downgrade drops all three columns. That loses which students had dropped,
so a downgrade returns the app to its old additive-only behaviour with the
whole roster active -- which is exactly the old behaviour, and no attendance
record is touched either way.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3b7f0d41a92'
down_revision: Union[str, Sequence[str], None] = 'f4c1a7b92d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('course_enrollments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dropped_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('roster_last_imported_at', sa.DateTime(),
                                      nullable=True))
        batch_op.add_column(sa.Column('roster_last_import_summary', sa.Text(),
                                      nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.drop_column('roster_last_import_summary')
        batch_op.drop_column('roster_last_imported_at')
    with op.batch_alter_table('course_enrollments', schema=None) as batch_op:
        batch_op.drop_column('dropped_at')
