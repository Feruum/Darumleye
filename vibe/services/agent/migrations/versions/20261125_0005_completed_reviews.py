"""Archive completed reviews for authenticated follow-up chat.

Revision ID: 20261125_0005
Revises: 20260716_0004
"""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0005"
down_revision = "20260716_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_completed_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_format", sa.String(3), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("requirements", sa.Integer(), nullable=False),
        sa.Column("findings", sa.Integer(), nullable=False),
        sa.Column("source_units", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
    )
    op.create_index("ix_agent_completed_reviews_owner_created", "agent_completed_reviews", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_completed_reviews_owner_created", table_name="agent_completed_reviews")
    op.drop_table("agent_completed_reviews")
