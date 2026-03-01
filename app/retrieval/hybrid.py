from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models import RepoChunk


@dataclass
class RetrievedSnippet:
    file_path: str
    start_line: int
    end_line: int
    commit_sha: str
    score: float
    chunk_text: str


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def reciprocal_rank_fusion(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank)


def _build_chunk_query(project_id: UUID, file_path_prefix: str | None, language: str | None) -> Select[tuple[RepoChunk]]:
    stmt = select(RepoChunk).where(RepoChunk.project_id == project_id, RepoChunk.is_active.is_(True))
    if file_path_prefix:
        stmt = stmt.where(RepoChunk.file_path.like(f"{file_path_prefix}%"))
    if language:
        stmt = stmt.where(RepoChunk.language == language)
    return stmt


def hybrid_search(
    db: Session,
    project_id: UUID,
    query: str,
    query_embedding: list[float],
    top_k: int,
    file_path_prefix: str | None = None,
    language: str | None = None,
    commit_sha: str | None = None,
) -> list[RetrievedSnippet]:
    rows = db.execute(_build_chunk_query(project_id, file_path_prefix, language).limit(2000)).scalars().all()

    if commit_sha:
        rows = [row for row in rows if row.commit_sha == commit_sha]

    query_tokens = set(query.lower().split())

    vector_ranked = sorted(
        rows,
        key=lambda row: cosine_similarity(query_embedding, row.embedding),
        reverse=True,
    )
    lexical_ranked = sorted(
        rows,
        key=lambda row: len(query_tokens.intersection(set(row.chunk_text.lower().split()))),
        reverse=True,
    )

    scores: dict[str, float] = defaultdict(float)
    by_id = {str(row.id): row for row in rows}

    for i, row in enumerate(vector_ranked[: max(top_k * 5, 20)], start=1):
        scores[str(row.id)] += reciprocal_rank_fusion(i)
    for i, row in enumerate(lexical_ranked[: max(top_k * 5, 20)], start=1):
        scores[str(row.id)] += reciprocal_rank_fusion(i)

    if file_path_prefix:
        for row in rows:
            if row.file_path.startswith(file_path_prefix):
                scores[str(row.id)] += 0.05
    if commit_sha:
        for row in rows:
            if row.commit_sha == commit_sha:
                scores[str(row.id)] += 0.05

    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]
    return [
        RetrievedSnippet(
            file_path=by_id[rid].file_path,
            start_line=by_id[rid].start_line,
            end_line=by_id[rid].end_line,
            commit_sha=by_id[rid].commit_sha,
            score=round(scores[rid], 6),
            chunk_text=by_id[rid].chunk_text,
        )
        for rid in ranked_ids
    ]
