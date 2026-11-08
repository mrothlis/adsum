"""remove the campus IP check

The campus-WiFi check is gone, along with the per-student IP address and the
in-range flag stored on every attendance record.

It was removed because it cannot do the job it exists for, and it was measured
failing in three separate ways on this project's own deployment.

1. It blocked the entire class. The check read the raw peer address
   (``request.client.host``) rather than going through the trusted-proxy-aware
   helper the login throttle uses. Behind the Cloudflare Tunnel that the
   install guide recommends, the peer is the tunnel itself: every student
   appeared to arrive from one address, the office machine's own. Measured
   from the live server log, every request carried the same
   ``2600:1700:...`` address. So configuring a campus range made
   ``check_ip_range`` return False for every student in the room.

2. Being on campus WiFi does not mean being in the classroom. The same
   network reaches the residence halls. A student in bed in their dorm is on
   campus WiFi and passes; the check cannot tell them from a student in the
   third row. It therefore never provided the assurance it appeared to.

3. Being in the classroom does not mean being on campus WiFi. A student whose
   phone is on cellular data, which is the common case for a quick scan, is
   refused while sitting in the room. The check's only observable effect on a
   correctly configured install was to block students who were present.

A control that cannot produce a true positive, cannot avoid a false positive,
and does produce false negatives is worse than no control, because it invites
an instructor to rely on it. The geofence stays: it measures distance from the
room rather than membership of a network, and an instructor can mark anyone
through manually.

Dropping ``attendance_records.ip_address`` and ``ip_in_range`` is also a
privacy improvement, and a straightforward one to explain in a data review.
Those columns recorded a network address against a named student for every
check-in. Behind a tunnel the value was identical on every row, so it
identified nothing while still looking like personal data, which is the worst
of both.

Revision ID: b8c4e1f29a07
Revises: e5a91c3f74b2
Create Date: 2026-11-07

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8c4e1f29a07'
down_revision: Union[str, Sequence[str], None] = 'e5a91c3f74b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table because SQLite cannot DROP COLUMN in older versions:
    # Alembic rebuilds the table and copies the remaining data.
    with op.batch_alter_table("attendance_records") as batch:
        batch.drop_column("ip_in_range")
        batch.drop_column("ip_address")
    with op.batch_alter_table("courses") as batch:
        batch.drop_column("allowed_ip_ranges")


def downgrade() -> None:
    # The columns come back empty. The addresses they held are not recovered,
    # deliberately: they were the tunnel's own address repeated on every row,
    # so there is nothing of value to restore and re-deriving them is
    # impossible anyway.
    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("allowed_ip_ranges", sa.Text(), nullable=True))
    with op.batch_alter_table("attendance_records") as batch:
        batch.add_column(sa.Column("ip_address", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("ip_in_range", sa.Boolean(), nullable=True))
