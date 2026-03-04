"""add api key rotation fields

Revision ID: 0004_api_key_rotation_fields
Revises: 0003_retrieval_indexes
Create Date: 2026-03-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_api_key_rotation_fields"
down_revision: str | None = "0003_retrieval_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("key_id", sa.String(length=32), nullable=True))
    op.add_column("api_keys", sa.Column("key_secret_hash", sa.String(length=255), nullable=True))
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))

    op.create_unique_constraint("uq_api_keys_key_id", "api_keys", ["key_id"])
    op.create_index("ix_api_keys_project_active", "api_keys", ["project_id", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_project_active", table_name="api_keys")
    op.drop_constraint("uq_api_keys_key_id", "api_keys", type_="unique")

    op.drop_column("api_keys", "revoked_at")
    op.drop_column("api_keys", "key_secret_hash")
    op.drop_column("api_keys", "key_id")
