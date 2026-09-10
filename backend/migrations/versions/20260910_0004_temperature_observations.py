"""Celsius bounds on batches and the temperature_observations entity."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.temperature import parse_temperature_zone

revision: str = "20260910_0004"
down_revision: str | None = "20260910_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _legacy_zones(bind) -> list:
    return bind.execute(
        sa.text("SELECT id, accession_number, temperature_zone FROM batches")
    ).all()


def upgrade() -> None:
    bind = op.get_bind()

    # Validate every legacy zone before touching the schema, so an unrecognised
    # value fails the migration cleanly on all dialects (PostgreSQL rolls the
    # DDL back transactionally; SQLite cannot roll back DDL, hence the ordering).
    unparsable: list[str] = []
    parsed: dict[str, tuple[float, float]] = {}
    for row in _legacy_zones(bind):
        try:
            parsed[row.id] = parse_temperature_zone(row.temperature_zone)
        except ValueError:
            unparsable.append(f"{row.accession_number} ({row.temperature_zone!r})")
    if unparsable:
        # Fail loudly rather than guessing bounds; the operator fixes the zone
        # text and reruns `alembic upgrade head`.
        raise RuntimeError(
            "Cannot migrate temperature zones that are not celsius ranges; "
            "fix the following batches first: " + "; ".join(unparsable)
        )

    # Add bounds as nullable so existing rows can be backfilled from legacy text.
    with op.batch_alter_table("batches", schema=None) as batch:
        batch.add_column(sa.Column("temp_min_c", sa.Float(), nullable=True))
        batch.add_column(sa.Column("temp_max_c", sa.Float(), nullable=True))

    for batch_id, (low, high) in parsed.items():
        bind.execute(
            sa.text(
                "UPDATE batches SET temp_min_c = :low, temp_max_c = :high WHERE id = :id"
            ),
            {"low": low, "high": high, "id": batch_id},
        )

    with op.batch_alter_table("batches", schema=None) as batch:
        batch.alter_column("temp_min_c", existing_type=sa.Float(), nullable=False)
        batch.alter_column("temp_max_c", existing_type=sa.Float(), nullable=False)
        batch.create_check_constraint("ck_batch_temp_bounds", "temp_min_c <= temp_max_c")

    op.create_table(
        "temperature_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column(
            "container_id", sa.String(36), sa.ForeignKey("containers.id"), nullable=False
        ),
        sa.Column("temperature_c", sa.Float(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum(
                "NORMAL",
                "OUT_OF_RANGE",
                name="temperatureverdict",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("measured_by", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "temperature_c >= -100 AND temperature_c <= 100",
            name="ck_observation_celsius_range",
        ),
    )
    op.create_index(
        "ix_temperature_observations_batch_id", "temperature_observations", ["batch_id"]
    )
    op.create_index(
        "ix_temperature_observations_container_id",
        "temperature_observations",
        ["container_id"],
    )

    # The single timeline event written per observation is linked 1:1 so retries or
    # later edits can never attach a second event to the same observation.
    with op.batch_alter_table("timeline_events", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "observation_id",
                sa.String(36),
                sa.ForeignKey(
                    "temperature_observations.id",
                    name="fk_timeline_events_observation",
                ),
                nullable=True,
            )
        )
    op.create_index(
        "uq_timeline_event_per_observation", "timeline_events", ["observation_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_timeline_event_per_observation", table_name="timeline_events")
    with op.batch_alter_table("timeline_events", schema=None) as batch:
        batch.drop_column("observation_id")

    op.drop_index(
        "ix_temperature_observations_container_id", table_name="temperature_observations"
    )
    op.drop_index(
        "ix_temperature_observations_batch_id", table_name="temperature_observations"
    )
    op.drop_table("temperature_observations")

    with op.batch_alter_table("batches", schema=None) as batch:
        batch.drop_constraint("ck_batch_temp_bounds", type_="check")
        batch.drop_column("temp_max_c")
        batch.drop_column("temp_min_c")
