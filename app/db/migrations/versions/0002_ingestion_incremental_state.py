"""incremental ingestion state and chunk metadata

Revision ID: 0002_ingestion_incremental_state
Revises: 0001_initial
Create Date: 2026-03-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_ingestion_incremental_state"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("repo_chunks", sa.Column("chunk_hash", sa.String(length=64), nullable=True))
    op.add_column("repo_chunks", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column(
        "repo_chunks",
        sa.Column("embedding_model", sa.String(length=128), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "repo_chunks",
        sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default="v1"),
    )

    op.execute("UPDATE repo_chunks SET chunk_hash = id::text")
    op.alter_column("repo_chunks", "chunk_hash", nullable=False)

    op.create_unique_constraint(
        "uq_repo_chunks_project_chunk_hash", "repo_chunks", ["project_id", "chunk_hash"]
    )
    op.create_index("ix_repo_chunks_project_active", "repo_chunks", ["project_id", "is_active"])

    op.add_column("ingestion_runs", sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_runs", sa.Column("chunks_upserted", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "ingestion_runs", sa.Column("chunks_deactivated", sa.Integer(), nullable=False, server_default="0")
    )

    op.create_table(
        "repo_ingestion_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repo_path", sa.Text(), nullable=False),
        sa.Column("last_successful_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "repo_path", name="uq_repo_ingestion_state_project_repo"),
    )


def downgrade() -> None:
    op.drop_table("repo_ingestion_state")

    op.drop_column("ingestion_runs", "chunks_deactivated")
    op.drop_column("ingestion_runs", "chunks_upserted")
    op.drop_column("ingestion_runs", "files_changed")

    op.drop_index("ix_repo_chunks_project_active", table_name="repo_chunks")
    op.drop_constraint("uq_repo_chunks_project_chunk_hash", "repo_chunks", type_="unique")

    op.drop_column("repo_chunks", "embedding_version")
    op.drop_column("repo_chunks", "embedding_model")
    op.drop_column("repo_chunks", "is_active")
    op.drop_column("repo_chunks", "chunk_hash")
