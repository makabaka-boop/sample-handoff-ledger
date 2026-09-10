"""Per-location submission sequence for inventory checks.

created_at alone cannot totalise submission order when the server clock
returns (or is frozen at) one instant, so recent-check replay must key off a
gapless per-location sequence assigned under the location lock.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0006"
down_revision: str | None = "20260910_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("location_inventory_checks", schema=None) as batch:
        batch.add_column(sa.Column("sequence_number", sa.Integer(), nullable=True))

    # Backfill each location's history in historical (id-independent) order so
    # every location starts gapless at 1..N.
    checks = bind.execute(
        sa.text(
            "SELECT id, location_id, created_at FROM location_inventory_checks "
            "ORDER BY location_id, created_at, id"
        )
    ).all()
    last_location: str | None = None
    next_number = 1
    for check_id, location_id, _created_at in checks:
        if location_id != last_location:
            last_location = location_id
            next_number = 1
        bind.execute(
            sa.text(
                "UPDATE location_inventory_checks SET sequence_number = :number "
                "WHERE id = :id"
            ),
            {"number": next_number, "id": check_id},
        )
        next_number += 1

    with op.batch_alter_table("location_inventory_checks", schema=None) as batch:
        batch.alter_column("sequence_number", existing_type=sa.Integer(), nullable=False)
    op.create_index(
        "uq_inventory_check_location_sequence",
        "location_inventory_checks",
        ["location_id", "sequence_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_inventory_check_location_sequence",
        table_name="location_inventory_checks",
    )
    with op.batch_alter_table("location_inventory_checks", schema=None) as batch:
        batch.drop_column("sequence_number")
