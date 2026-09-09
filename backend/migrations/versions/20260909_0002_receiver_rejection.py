"""Receiver rejection fields on handoffs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0002"
down_revision: str | None = "20260909_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("handoffs", schema=None) as batch:
        batch.add_column(sa.Column("rejected_by", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("reject_reason", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("reject_note", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_handoffs_reject_reason",
            (
                "reject_reason IS NULL OR reject_reason IN "
                "('seal_broken', 'label_mismatch', 'package_contaminated', 'other')"
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("handoffs", schema=None) as batch:
        batch.drop_constraint("ck_handoffs_reject_reason", type_="check")
        batch.drop_column("reject_note")
        batch.drop_column("reject_reason")
        batch.drop_column("rejected_at")
        batch.drop_column("rejected_by")
