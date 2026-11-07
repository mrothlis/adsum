"""late credit percent replaces late counts present

Revision ID: 53f71517231c
Revises: b21de9af0723
Create Date: 2026-11-06 19:03:23.164784

A late arrival used to be all-or-nothing: late_counts_present true meant a
full class, false meant an absence. It is now a percentage, so a professor can
say "half credit" -- which is the realistic case, because the QR code is only
projected at the start of class and late arrivals are marked by hand.

The data is carried across rather than defaulted, which matters: a course that
had deliberately set late_counts_present = false would otherwise silently
start awarding full credit for late arrivals, changing grades already in D2L
with no visible cause.

  late_counts_present = 0  ->  late_credit_percent = 0    (explicit: no credit)
  late_counts_present = 1  ->  late_credit_percent = NULL (inherit default 100)

NULL is used for the true case rather than a literal 100 so the course stays
on the default and follows it if the default ever changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '53f71517231c'
down_revision: Union[str, Sequence[str], None] = 'b21de9af0723'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the percentage, carry the old boolean into it, drop the boolean."""
    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('late_credit_percent', sa.Integer(), nullable=True))

    # Translate before dropping the source column.
    op.execute(
        "UPDATE courses SET late_credit_percent = 0 "
        "WHERE late_counts_present = 0"
    )

    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.drop_column('late_counts_present')


def downgrade() -> None:
    """Reverse it, mapping any partial credit back to the nearest boolean."""
    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('late_counts_present', sa.BOOLEAN(),
                                      server_default=sa.text("'1'"), nullable=False))

    # Anything with no credit becomes false; partial credit cannot be
    # represented by a boolean, so it rounds to whether it was worth at least
    # half a class. Downgrading is therefore lossy, which is expected.
    op.execute(
        "UPDATE courses SET late_counts_present = 0 "
        "WHERE late_credit_percent IS NOT NULL AND late_credit_percent < 50"
    )

    with op.batch_alter_table('courses', schema=None) as batch_op:
        batch_op.drop_column('late_credit_percent')
