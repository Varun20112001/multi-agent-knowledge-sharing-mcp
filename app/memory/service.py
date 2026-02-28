from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Memory, RepoChunk
from app.embeddings.provider import EmbeddingProvider
from app.schemas.api import Citation, StoreMemoryRequest


class MemoryValidationError(ValueError):
    pass


def _citation_resolves(db: Session, project_id: UUID, citation: Citation) -> bool:
    stmt = select(RepoChunk).where(
        RepoChunk.project_id == project_id,
        RepoChunk.file_path == citation.file_path,
        RepoChunk.start_line <= citation.line_start,
        RepoChunk.end_line >= citation.line_start,
    )
    return db.execute(stmt.limit(1)).scalar_one_or_none() is not None


def validate_memory_request(db: Session, payload: StoreMemoryRequest) -> None:
    if not payload.citations:
        raise MemoryValidationError("At least one citation is required")
    for citation in payload.citations:
        if not _citation_resolves(db, payload.project_id, citation):
            raise MemoryValidationError(
                f"Citation cannot be resolved: {citation.file_path}:{citation.line_start}"
            )


def store_memory(db: Session, payload: StoreMemoryRequest, embedder: EmbeddingProvider, source_commit_sha: str) -> Memory:
    validate_memory_request(db, payload)

    existing = db.execute(
        select(Memory).where(
            Memory.project_id == payload.project_id,
            Memory.subject == payload.subject,
            Memory.status == "active",
        )
    ).scalars().all()

    for mem in existing:
        if mem.fact != payload.fact:
            mem.status = "superseded"

    embedding = embedder.embed([f"{payload.subject}\n{payload.fact}\n{payload.reason or ''}"])[0]
    memory = Memory(
        project_id=payload.project_id,
        subject=payload.subject,
        fact=payload.fact,
        reason=payload.reason,
        citations=[citation.model_dump() for citation in payload.citations],
        source_commit_sha=source_commit_sha,
        status="active",
        confidence=payload.confidence,
        embedding=embedding,
    )
    db.add(memory)
    db.flush()
    return memory


def search_memory(
    db: Session,
    project_id: UUID,
    query: str,
    query_embedding: list[float],
    top_k: int,
    include_stale: bool,
) -> list[Memory]:
    statuses = ["active", "superseded", "stale"] if include_stale else ["active"]
    rows = db.execute(
        select(Memory).where(Memory.project_id == project_id, Memory.status.in_(statuses))
    ).scalars().all()

    query_tokens = set(query.lower().split())

    def score(mem: Memory) -> float:
        token_hits = len(query_tokens.intersection(set(f"{mem.subject} {mem.fact}".lower().split())))
        dot = sum(x * y for x, y in zip(mem.embedding, query_embedding, strict=False))
        return float(token_hits) + dot

    return sorted(rows, key=score, reverse=True)[:top_k]


def verify_memory(db: Session, project_id: UUID, memory_id: UUID) -> tuple[str, str, list[str]]:
    memory = db.execute(
        select(Memory).where(Memory.id == memory_id, Memory.project_id == project_id)
    ).scalar_one()

    before = memory.status
    checks: list[str] = []

    for citation in memory.citations:
        file_path = citation.get("file_path")
        line_start = citation.get("line_start")
        if not isinstance(file_path, str) or not isinstance(line_start, int):
            checks.append("Invalid citation format")
            memory.status = "stale"
            continue

        found = db.execute(
            select(RepoChunk).where(
                RepoChunk.project_id == project_id,
                RepoChunk.file_path == file_path,
                RepoChunk.start_line <= line_start,
                RepoChunk.end_line >= line_start,
            )
        ).scalar_one_or_none()

        if found is None:
            checks.append(f"Missing citation: {file_path}:{line_start}")
            memory.status = "stale"
        else:
            checks.append(f"Citation OK: {file_path}:{line_start}")

    memory.last_verified_at = datetime.now(timezone.utc)
    after = memory.status
    return before, after, checks
