"""Allow Office formats in the completed-review archive.

Revision ID: 20261125_0006
Revises: 20261125_0005
"""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0006"
down_revision = "20261125_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_completed_reviews") as batch:
        batch.alter_column(
            "source_format",
            existing_type=sa.String(3),
            type_=sa.String(8),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Refuse to silently truncate existing DOCX/XLSX format identifiers.
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM agent_completed_reviews WHERE length(source_format) > 3"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("Cannot narrow source_format while Office reviews exist")
    with op.batch_alter_table("agent_completed_reviews") as batch:
        batch.alter_column(
            "source_format",
            existing_type=sa.String(8),
            type_=sa.String(3),
            existing_nullable=False,
        )
