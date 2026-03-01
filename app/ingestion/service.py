from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import IngestionRun, RepoChunk
from app.embeddings.provider import EmbeddingProvider
from app.ingestion.chunker import chunk_text_by_lines
from app.ingestion.loader import RepoFile, get_head_commit_sha, load_repo_files, should_exclude
from app.ingestion.state import acquire_repo_lock, read_last_successful_commit, write_last_successful_commit
from app.schemas.api import IngestRepoRequest

EMBEDDING_MODEL = "default"
EMBEDDING_VERSION = "v1"


@dataclass
class IngestionDiff:
    unchanged_commit: bool
    changed_files: list[RepoFile]
    changed_paths: list[str]
    deleted_paths: list[str]


def _compute_chunk_hash(project_id: UUID, repo_path: str, file_path: str, chunk_index: int, chunk_text: str) -> str:
    payload = f"{project_id}:{repo_path}:{file_path}:{chunk_index}:{chunk_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _git_diff_paths(repo_root: Path, from_commit: str, to_commit: str) -> tuple[set[str], set[str]]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-status", from_commit, to_commit],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return set(), set()

    changed: set[str] = set()
    deleted: set[str] = set()
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        status, _, remainder = line.partition("\t")
        if not remainder:
            continue
        # rename/copy lines include multiple paths separated by tabs; the final path is the current one.
        path = remainder.split("\t")[-1]
        normalized = path.replace("\\", "/")
        if status.startswith("D"):
            deleted.add(normalized)
            continue
        changed.add(normalized)
    return changed, deleted


def _compute_diff(db: Session, payload: IngestRepoRequest, repo_root: Path, head_sha: str) -> IngestionDiff:
    last_success_sha = read_last_successful_commit(db, payload.project_id, repo_root)
    if last_success_sha and last_success_sha == head_sha:
        return IngestionDiff(unchanged_commit=True, changed_files=[], changed_paths=[], deleted_paths=[])

    all_files = load_repo_files(
        repo_path=str(repo_root),
        include_globs=payload.include_globs,
        exclude_globs=payload.exclude_globs,
        max_file_size_kb=payload.max_file_size_kb,
    )
    files_by_path = {item.file_path: item for item in all_files}

    if not last_success_sha:
        changed_paths = sorted(files_by_path)
        return IngestionDiff(
            unchanged_commit=False,
            changed_files=[files_by_path[path] for path in changed_paths],
            changed_paths=changed_paths,
            deleted_paths=[],
        )

    changed, deleted = _git_diff_paths(repo_root, last_success_sha, head_sha)

    def _allowed(path: str) -> bool:
        rel = Path(path)
        if should_exclude(rel, payload.exclude_globs):
            return False
        if not payload.include_globs:
            return True
        return any(rel.match(pattern) for pattern in payload.include_globs)

    changed_paths = sorted(path for path in changed if _allowed(path) and path in files_by_path)
    deleted_paths = sorted(path for path in deleted if _allowed(path))

    return IngestionDiff(
        unchanged_commit=False,
        changed_files=[files_by_path[path] for path in changed_paths],
        changed_paths=changed_paths,
        deleted_paths=deleted_paths,
    )


def _deactivate_file_paths(db: Session, project_id: UUID, repo_root: Path, file_paths: list[str]) -> int:
    if not file_paths:
        return 0
    result = db.execute(
        update(RepoChunk)
        .where(
            RepoChunk.project_id == project_id,
            RepoChunk.repo_path == str(repo_root),
            RepoChunk.file_path.in_(file_paths),
            RepoChunk.is_active.is_(True),
        )
        .values(is_active=False)
    )
    return int(result.rowcount or 0)


def _process_changed_files(
    db: Session,
    payload: IngestRepoRequest,
    repo_root: Path,
    head_sha: str,
    changed_files: list[RepoFile],
    embedder: EmbeddingProvider,
) -> int:
    records: list[dict[str, object]] = []
    texts: list[str] = []

    for repo_file in changed_files:
        chunks = chunk_text_by_lines(repo_file.content)
        for chunk in chunks:
            texts.append(chunk.text)
            records.append(
                {
                    "project_id": payload.project_id,
                    "repo_path": str(repo_root),
                    "file_path": repo_file.file_path,
                    "language": repo_file.language,
                    "chunk_text": chunk.text,
                    "chunk_hash": _compute_chunk_hash(
                        payload.project_id,
                        str(repo_root),
                        repo_file.file_path,
                        chunk.chunk_index,
                        chunk.text,
                    ),
                    "chunk_index": chunk.chunk_index,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "commit_sha": head_sha,
                    "is_active": True,
                    "embedding_model": EMBEDDING_MODEL,
                    "embedding_version": EMBEDDING_VERSION,
                    "embedding": [],
                    "tsv": func.to_tsvector("simple", chunk.text),
                }
            )

    if not records:
        return 0

    vectors = embedder.embed(texts)
    for row, vector in zip(records, vectors, strict=False):
        row["embedding"] = vector

    stmt = insert(RepoChunk).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RepoChunk.project_id, RepoChunk.chunk_hash],
        set_={
            "repo_path": stmt.excluded.repo_path,
            "file_path": stmt.excluded.file_path,
            "language": stmt.excluded.language,
            "chunk_text": stmt.excluded.chunk_text,
            "chunk_index": stmt.excluded.chunk_index,
            "start_line": stmt.excluded.start_line,
            "end_line": stmt.excluded.end_line,
            "commit_sha": stmt.excluded.commit_sha,
            "embedding": stmt.excluded.embedding,
            "is_active": True,
            "embedding_model": stmt.excluded.embedding_model,
            "embedding_version": stmt.excluded.embedding_version,
            "tsv": stmt.excluded.tsv,
        },
    )
    db.execute(stmt)
    return len(records)


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


def _finalize_run_state(run: IngestionRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = datetime.now(timezone.utc)


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
        acquire_repo_lock(db, payload.project_id, repo_root)

        # phase 1: compute diff
        diff = _compute_diff(db, payload, repo_root, head_sha)
        run.files_scanned = len(diff.changed_files)
        run.files_changed = len(diff.changed_paths) + len(diff.deleted_paths)

        if diff.unchanged_commit:
            _finalize_run_state(run, status="success")
            return run

        # phase 2: process changed files
        deactivated_from_changed = _deactivate_file_paths(db, payload.project_id, repo_root, diff.changed_paths)
        chunks_upserted = _process_changed_files(db, payload, repo_root, head_sha, diff.changed_files, embedder)

        # phase 3: process deletions
        deactivated_from_deletes = _deactivate_file_paths(db, payload.project_id, repo_root, diff.deleted_paths)

        # phase 4: finalize state
        run.chunks_upserted = chunks_upserted
        run.chunks_deactivated = deactivated_from_changed + deactivated_from_deletes
        run.chunks_written = chunks_upserted
        write_last_successful_commit(db, payload.project_id, repo_root, head_sha)
        _finalize_run_state(run, status="success")
    except Exception as exc:
        _finalize_run_state(run, status="failed", error=str(exc)[:4000])

    return run


def ingest_repository(db: Session, payload: IngestRepoRequest, embedder: EmbeddingProvider) -> IngestionRun:
    run = create_ingestion_run(db, payload)
    return execute_ingestion_run(db, run.id, payload, embedder)
