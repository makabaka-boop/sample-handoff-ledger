"""Immutable location inventory checks and their classified detail rows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0005"
down_revision: str | None = "20260910_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "location_inventory_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "location_id", sa.String(36), sa.ForeignKey("locations.id"), nullable=False
        ),
        sa.Column("checked_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("misplaced_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("matched_count >= 0", name="ck_inventory_check_matched_nonneg"),
        sa.CheckConstraint("missing_count >= 0", name="ck_inventory_check_missing_nonneg"),
        sa.CheckConstraint(
            "misplaced_count >= 0", name="ck_inventory_check_misplaced_nonneg"
        ),
        sa.CheckConstraint("unknown_count >= 0", name="ck_inventory_check_unknown_nonneg"),
        sa.CheckConstraint("scanned_count >= 0", name="ck_inventory_check_scanned_nonneg"),
    )
    op.create_index(
        "ix_location_inventory_checks_location_id",
        "location_inventory_checks",
        ["location_id"],
    )
    op.create_table(
        "location_inventory_check_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "check_id",
            sa.String(36),
            sa.ForeignKey("location_inventory_checks.id"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "MATCHED",
                "MISSING",
                "MISPLACED",
                "UNKNOWN",
                name="inventorycheckcategory",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("scanned_label", sa.String(100)),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("container_id", sa.String(36)),
        sa.Column("batch_id", sa.String(36)),
        sa.Column("accession_number", sa.String(80)),
        sa.Column("container_label", sa.String(100)),
        sa.Column("recorded_location_id", sa.String(36)),
        sa.Column("recorded_location_code", sa.String(40)),
        sa.Column("recorded_location_name", sa.String(100)),
        sa.CheckConstraint("line_number > 0", name="ck_inventory_check_item_positive_line"),
    )
    op.create_index(
        "ix_location_inventory_check_items_check_id",
        "location_inventory_check_items",
        ["check_id"],
    )
    op.create_index(
        "uq_inventory_check_category_line",
        "location_inventory_check_items",
        ["check_id", "category", "line_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_inventory_check_category_line", table_name="location_inventory_check_items"
    )
    op.drop_index(
        "ix_location_inventory_check_items_check_id",
        table_name="location_inventory_check_items",
    )
    op.drop_table("location_inventory_check_items")
    op.drop_index(
        "ix_location_inventory_checks_location_id",
        table_name="location_inventory_checks",
    )
    op.drop_table("location_inventory_checks")
