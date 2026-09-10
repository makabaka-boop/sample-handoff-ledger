"""Reroute provenance for pending handoffs.

All four columns stay nullable so every pre-reroute record keeps reading
exactly as before: a NULL original target simply means the handoff was
never rerouted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0007"
down_revision: str | None = "20260910_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("handoffs", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "original_to_location_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "locations.id", name="fk_handoffs_original_to_location"
                ),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("rerouted_by", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("rerouted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("reroute_reason", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("handoffs", schema=None) as batch:
        batch.drop_column("reroute_reason")
        batch.drop_column("rerouted_at")
        batch.drop_column("rerouted_by")
        batch.drop_column("original_to_location_id")
