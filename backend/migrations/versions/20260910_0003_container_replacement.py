"""Container active/replaced lifecycle and replacement provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0003"
down_revision: str | None = "20260909_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("containers", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.Enum(
                    "ACTIVE",
                    "REPLACED",
                    name="containerstatus",
                    native_enum=False,
                ),
                # Existing containers predate transloading; they stay in circulation.
                nullable=False,
                server_default="ACTIVE",
            )
        )
        batch.add_column(
            sa.Column(
                "replacement_container_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "containers.id", name="fk_containers_replacement_container"
                ),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("replaced_by", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("replacement_reason", sa.String(length=200), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("containers", schema=None) as batch:
        batch.drop_column("replacement_reason")
        batch.drop_column("replaced_at")
        batch.drop_column("replaced_by")
        batch.drop_column("replacement_container_id")
        batch.drop_column("status")
