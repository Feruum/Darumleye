"""Persist bounded review input and worker leases across restarts.

Revision ID: 20261125_0007
Revises: 20261125_0006
"""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0007"
down_revision = "20261125_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Local startup also creates missing tables through Base.metadata. The
    # migration can be applied before or after that first startup.
    op.create_table(
        "agent_spec_review_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remaining_seconds", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("active_owner", sa.String(255)),
        sa.Column("active_slot", sa.Integer()),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("payload_metadata", sa.JSON()),
        sa.Column("primary_bytes", sa.LargeBinary()),
        sa.Column("reference_bytes", sa.LargeBinary()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(2048)),
        sa.UniqueConstraint("active_owner", name="uq_spec_review_job_owner"),
        sa.UniqueConstraint("active_slot", name="uq_spec_review_job_slot"),
        sa.CheckConstraint(
            "active_slot IS NULL OR active_slot BETWEEN 1 AND 2",
            name="ck_spec_review_job_slot",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_spec_review_job_status",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_spec_review_jobs_status_lease",
        "agent_spec_review_jobs",
        ["status", "lease_until"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_spec_review_jobs_owner",
        "agent_spec_review_jobs",
        ["owner_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM agent_spec_review_jobs WHERE active_slot IS NOT NULL"
            )
        )
        .scalar_one()
    ):
        raise RuntimeError("Cannot remove review jobs while unfinished reviews exist")
    op.drop_index(
        "ix_spec_review_jobs_status_lease", table_name="agent_spec_review_jobs"
    )
    op.drop_index("ix_spec_review_jobs_owner", table_name="agent_spec_review_jobs")
    op.drop_table("agent_spec_review_jobs")
