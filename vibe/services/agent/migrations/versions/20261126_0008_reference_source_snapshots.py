"""Preserve parsed reference original snapshots alongside archived reports.

Revision ID: 20261126_0008
Revises: 20261125_0007
"""

from alembic import op
import sqlalchemy as sa

revision = "20261126_0008"
down_revision = "20261125_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Previous review records intentionally remain NULL: their reference inputs
    # are not present and must not be reconstructed from quotations.
    op.add_column("agent_completed_reviews", sa.Column("reference_source_units", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_completed_reviews") as batch:
        batch.drop_column("reference_source_units")
