"""add retrieval support indexes

Revision ID: 0003_retrieval_indexes
Revises: 0002_ingestion_incremental_state
Create Date: 2026-03-01
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_retrieval_indexes"
down_revision: str | None = "0002_ingestion_incremental_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_repo_chunks_project_active ON repo_chunks (project_id, is_active)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_repo_chunks_tsv_gin ON repo_chunks USING GIN (tsv)"
    )
    op.execute("DROP INDEX IF EXISTS ix_repo_chunks_tsv")

    op.execute("DROP INDEX IF EXISTS ix_repo_chunks_embedding_hnsw")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_repo_chunks_embedding_hnsw_tuned
        ON repo_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 128)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_repo_chunks_embedding_hnsw_tuned")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_repo_chunks_embedding_hnsw ON repo_chunks USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute("DROP INDEX IF EXISTS ix_repo_chunks_tsv_gin")
    op.execute("CREATE INDEX IF NOT EXISTS ix_repo_chunks_tsv ON repo_chunks USING GIN (tsv)")
