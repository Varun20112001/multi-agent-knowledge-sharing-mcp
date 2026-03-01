from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import IngestionRun, RepoChunk
from app.embeddings.provider import EmbeddingProvider
from app.ingestion.chunker import chunk_text_by_lines
from app.ingestion.loader import get_head_commit_sha, load_repo_files
from app.schemas.api import IngestRepoRequest


def create_ingestion_run(db: Session, payload: IngestRepoRequest) -> IngestionRun:
    repo_root = Path(payload.repo_path).resolve()
    head_sha = get_head_commit_sha(repo_root)

    run = IngestionRun(
        project_id=payload.project_id,
        repo_path=str(repo_root),
        head_commit_sha=head_sha,
        status="running",
    )
    db.add(run)
    db.flush()
    return run


def execute_ingestion_run(
    db: Session,
    run_id: UUID,
    payload: IngestRepoRequest,
    embedder: EmbeddingProvider,
) -> IngestionRun:
    run = db.get(IngestionRun, run_id)
    if run is None:
        raise ValueError(f"Ingestion run not found: {run_id}")

    repo_root = Path(payload.repo_path).resolve()
    head_sha = run.head_commit_sha
    run.status = "running"
    run.error = None

    try:
        files = load_repo_files(
            repo_path=payload.repo_path,
            include_globs=payload.include_globs,
            exclude_globs=payload.exclude_globs,
            max_file_size_kb=payload.max_file_size_kb,
        )

        run.files_scanned = len(files)

        db.execute(
            delete(RepoChunk).where(
                RepoChunk.project_id == payload.project_id,
                RepoChunk.repo_path == str(repo_root),
            )
        )

        chunks_to_store: list[RepoChunk] = []
        texts: list[str] = []

        for repo_file in files:
            chunks = chunk_text_by_lines(repo_file.content)
            for chunk in chunks:
                texts.append(chunk.text)
                chunks_to_store.append(
                    RepoChunk(
                        project_id=payload.project_id,
                        repo_path=repo_file.repo_path,
                        file_path=repo_file.file_path,
                        language=repo_file.language,
                        chunk_text=chunk.text,
                        chunk_index=chunk.chunk_index,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        commit_sha=head_sha,
                        embedding=[],
                        tsv=func.to_tsvector("simple", chunk.text),
                    )
                )

        vectors = embedder.embed(texts) if texts else []
        for chunk_obj, vector in zip(chunks_to_store, vectors, strict=False):
            chunk_obj.embedding = vector
            db.add(chunk_obj)

        run.chunks_written = len(chunks_to_store)
        run.status = "success"
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)[:4000]

    run.finished_at = datetime.now(timezone.utc)
    return run


def ingest_repository(db: Session, payload: IngestRepoRequest, embedder: EmbeddingProvider) -> IngestionRun:
    run = create_ingestion_run(db, payload)
    return execute_ingestion_run(db, run.id, payload, embedder)
