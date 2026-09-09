"""Initial sample handoff ledger schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_cold_storage", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("accession_number", sa.String(80), nullable=False, unique=True),
        sa.Column("temperature_zone", sa.String(40), nullable=False),
        sa.Column("max_out_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "disposition",
            sa.Enum(
                "ACTIVE",
                "ISOLATED",
                "REVIEW",
                "RELEASED",
                name="batchdisposition",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_out_minutes > 0", name="ck_batch_positive_max_out"),
    )
    op.create_table(
        "containers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column(
            "current_location_id", sa.String(36), sa.ForeignKey("locations.id"), nullable=False
        ),
        sa.Column("accumulated_out_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("out_since", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "accumulated_out_seconds >= 0", name="ck_container_nonnegative_exposure"
        ),
    )
    op.create_index("ix_containers_batch_id", "containers", ["batch_id"])
    op.create_index(
        "uq_container_label_per_batch", "containers", ["batch_id", "label"], unique=True
    )
    op.create_table(
        "handoffs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("container_id", sa.String(36), sa.ForeignKey("containers.id"), nullable=False),
        sa.Column("from_location_id", sa.String(36), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("to_location_id", sa.String(36), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("received_by", sa.String(100)),
        sa.Column("cancelled_by", sa.String(100)),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RECEIVED",
                "CANCELLED",
                "ANOMALY",
                name="handoffstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("anomaly_at", sa.DateTime(timezone=True)),
        sa.Column("anomaly_reason", sa.String(80)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.String(80)),
        sa.Column("successor_id", sa.String(36), sa.ForeignKey("handoffs.id")),
    )
    op.create_index("ix_handoffs_batch_id", "handoffs", ["batch_id"])
    op.create_index("ix_handoffs_container_id", "handoffs", ["container_id"])
    op.create_index(
        "uq_one_pending_handoff_per_container",
        "handoffs",
        ["container_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_table(
        "timeline_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("container_id", sa.String(36), sa.ForeignKey("containers.id")),
        sa.Column("handoff_id", sa.String(36), sa.ForeignKey("handoffs.id")),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text()),
    )
    op.create_index("ix_timeline_events_batch_id", "timeline_events", ["batch_id"])
    op.create_index("ix_timeline_events_container_id", "timeline_events", ["container_id"])
    op.create_index("ix_timeline_events_handoff_id", "timeline_events", ["handoff_id"])
    locations = sa.table(
        "locations",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("is_cold_storage", sa.Boolean),
    )
    op.bulk_insert(
        locations,
        [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "code": "FRIDGE",
                "name": "冷藏冰箱",
                "is_cold_storage": True,
            },
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "code": "BENCH",
                "name": "处理台",
                "is_cold_storage": False,
            },
            {
                "id": "00000000-0000-0000-0000-000000000003",
                "code": "WINDOW",
                "name": "交接窗",
                "is_cold_storage": False,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("timeline_events")
    op.drop_index("uq_one_pending_handoff_per_container", table_name="handoffs")
    op.drop_table("handoffs")
    op.drop_table("containers")
    op.drop_table("batches")
    op.drop_table("locations")
