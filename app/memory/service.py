from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Memory, RepoChunk
from app.embeddings.provider import EmbeddingProvider
from app.schemas.api import Citation, CitationInput, StoreMemoryRequest


class MemoryValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "details": self.details}


def _normalized_path(raw_path: str) -> str:
    cleaned = raw_path.strip().replace("\\", "/").strip("/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    return cleaned


def _project_has_chunks(db: Session, project_id: UUID) -> bool:
    return (
        db.execute(select(RepoChunk.id).where(RepoChunk.project_id == project_id).limit(1)).scalar_one_or_none()
        is not None
    )


def list_project_files(
    db: Session,
    project_id: UUID,
    prefix: str | None = None,
    limit: int = 200,
) -> list[str]:
    stmt = select(RepoChunk.file_path).where(RepoChunk.project_id == project_id).distinct()
    rows = [row[0] for row in db.execute(stmt).all()]

    if prefix:
        normalized_prefix = _normalized_path(prefix)
        rows = [path for path in rows if _normalized_path(path).startswith(normalized_prefix)]

    rows = sorted(rows)
    return rows[:limit]


def _resolve_file_path(
    db: Session,
    project_id: UUID,
    raw_path: str,
) -> tuple[str | None, list[str]]:
    candidates = list_project_files(db, project_id, limit=5000)
    normalized_raw = _normalized_path(raw_path)

    exact = [path for path in candidates if _normalized_path(path) == normalized_raw]
    if exact:
        return exact[0], []

    suffix = [path for path in candidates if _normalized_path(path).endswith(normalized_raw)]
    if len(suffix) == 1:
        return suffix[0], []
    if len(suffix) > 1:
        return None, suffix[:10]

    return None, []


def _resolve_citation(db: Session, project_id: UUID, citation: CitationInput) -> Citation:
    raw_path = citation.file_path or citation.url
    if not raw_path:
        raise MemoryValidationError(
            code="INVALID_SCHEMA",
            message="Each citation must include file_path (or url compatibility field).",
            details={
                "citation": citation.model_dump(),
                "expected": {
                    "file_path": "repo/relative/path.py",
                    "line_start": 1,
                    "line_end": 10,
                },
            },
        )

    resolved_path, suggestions = _resolve_file_path(db, project_id, raw_path)
    if resolved_path is None:
        raise MemoryValidationError(
            code="CITATION_PATH_NOT_FOUND",
            message=f"Citation path cannot be resolved: {raw_path}",
            details={"input_path": raw_path, "suggestions": suggestions},
        )

    line_start = citation.line_start or 1
    line_end = citation.line_end

    line_match = db.execute(
        select(RepoChunk.id).where(
            RepoChunk.project_id == project_id,
            RepoChunk.file_path == resolved_path,
            RepoChunk.start_line <= line_start,
            RepoChunk.end_line >= line_start,
        )
    ).scalar_one_or_none()

    if line_match is None:
        raise MemoryValidationError(
            code="CITATION_LINE_OUT_OF_RANGE",
            message=f"Citation line cannot be resolved: {resolved_path}:{line_start}",
            details={"file_path": resolved_path, "line_start": line_start},
        )

    return Citation(file_path=resolved_path, line_start=line_start, line_end=line_end, quote=citation.quote)


def normalize_citations(db: Session, project_id: UUID, citations: list[CitationInput]) -> list[Citation]:
    if not citations:
        raise MemoryValidationError(code="INVALID_SCHEMA", message="At least one citation is required")

    if not _project_has_chunks(db, project_id):
        raise MemoryValidationError(
            code="PROJECT_NOT_INGESTED",
            message="No indexed chunks found for this project. Run repository ingestion first.",
        )

    normalized: list[Citation] = []
    for citation in citations:
        normalized.append(_resolve_citation(db, project_id, citation))
    return normalized


def validate_memory_request(db: Session, payload: StoreMemoryRequest) -> list[Citation]:
    return normalize_citations(db, payload.project_id, payload.citations)


def validate_citations(
    db: Session,
    project_id: UUID,
    citations: list[CitationInput],
) -> dict[str, object]:
    result: list[dict[str, object]] = []
    normalized: list[dict[str, object]] = []

    for citation in citations:
        try:
            resolved = _resolve_citation(db, project_id, citation)
            result.append({"valid": True, "input": citation.model_dump(), "resolved": resolved.model_dump()})
            normalized.append(resolved.model_dump())
        except MemoryValidationError as exc:
            result.append(
                {
                    "valid": False,
                    "input": citation.model_dump(),
                    "error": exc.as_dict(),
                }
            )

    return {
        "valid": all(item["valid"] for item in result),
        "results": result,
        "normalized_citations": normalized,
    }


def store_memory(db: Session, payload: StoreMemoryRequest, embedder: EmbeddingProvider, source_commit_sha: str) -> Memory:
    normalized_citations = validate_memory_request(db, payload)

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
        citations=[citation.model_dump() for citation in normalized_citations],
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
