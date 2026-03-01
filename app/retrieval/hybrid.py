from __future__ import annotations

import base64
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.db.models import RepoChunk


@dataclass
class RetrievedSnippet:
    id: str
    file_path: str
    start_line: int
    end_line: int
    commit_sha: str
    score: float
    chunk_text: str


@dataclass
class RetrievalPage:
    items: list[RetrievedSnippet]
    next_cursor: str | None


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


def _encode_cursor(score: float, chunk_id: str) -> str:
    payload = {"score": round(score, 12), "id": chunk_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    if not cursor:
        return None, None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        data = json.loads(decoded)
        return float(data["score"]), str(data["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, None


def hybrid_search_legacy(
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
            id=rid,
            file_path=by_id[rid].file_path,
            start_line=by_id[rid].start_line,
            end_line=by_id[rid].end_line,
            commit_sha=by_id[rid].commit_sha,
            score=round(scores[rid], 6),
            chunk_text=by_id[rid].chunk_text,
        )
        for rid in ranked_ids
    ]


def hybrid_search(
    db: Session,
    project_id: UUID,
    query: str,
    query_embedding: list[float],
    top_k: int,
    file_path_prefix: str | None = None,
    language: str | None = None,
    commit_sha: str | None = None,
    cursor: str | None = None,
    candidate_pool_size: int | None = None,
) -> RetrievalPage:
    candidate_limit = candidate_pool_size or max(top_k * 8, 40)
    cursor_score, cursor_id = _decode_cursor(cursor)

    stmt = text(
            """
            WITH base_chunks AS (
                SELECT id, file_path, start_line, end_line, commit_sha, chunk_text, embedding, tsv
                FROM repo_chunks
                WHERE project_id = :project_id
                  AND is_active = TRUE
                  AND (:file_path_prefix IS NULL OR file_path LIKE (:file_path_prefix || '%'))
                  AND (:language IS NULL OR language = :language)
                  AND (:commit_sha IS NULL OR commit_sha = :commit_sha)
            ),
            vector_candidates AS (
                SELECT
                    id,
                    row_number() OVER (ORDER BY embedding <=> CAST(:query_embedding AS vector), id ASC) AS rank
                FROM base_chunks
                ORDER BY embedding <=> CAST(:query_embedding AS vector), id ASC
                LIMIT :candidate_limit
            ),
            lexical_candidates AS (
                SELECT
                    id,
                    row_number() OVER (
                        ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', :query)) DESC, id ASC
                    ) AS rank
                FROM base_chunks
                WHERE tsv @@ websearch_to_tsquery('english', :query)
                ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', :query)) DESC, id ASC
                LIMIT :candidate_limit
            ),
            fused AS (
                SELECT
                    c.id,
                    c.file_path,
                    c.start_line,
                    c.end_line,
                    c.commit_sha,
                    c.chunk_text,
                    SUM(
                        CASE
                            WHEN src.rank IS NULL THEN 0
                            ELSE 1.0 / (:rrf_k + src.rank)
                        END
                    )
                    + CASE WHEN :file_path_prefix IS NOT NULL AND c.file_path LIKE (:file_path_prefix || '%') THEN 0.05 ELSE 0 END
                    + CASE WHEN :commit_sha IS NOT NULL AND c.commit_sha = :commit_sha THEN 0.05 ELSE 0 END AS score
                FROM base_chunks c
                JOIN (
                    SELECT id, rank FROM vector_candidates
                    UNION ALL
                    SELECT id, rank FROM lexical_candidates
                ) AS src ON src.id = c.id
                GROUP BY c.id, c.file_path, c.start_line, c.end_line, c.commit_sha, c.chunk_text
            )
            SELECT id::text AS id, file_path, start_line, end_line, commit_sha, chunk_text, score
            FROM fused
            WHERE :cursor_score IS NULL
               OR score < :cursor_score
               OR (score = :cursor_score AND id::text > :cursor_id)
            ORDER BY score DESC, id ASC
            LIMIT :limit_plus_one
            """
    )

    params: dict[str, object] = {
        "project_id": str(project_id),
        "query": query,
        "query_embedding": str(query_embedding),
        "candidate_limit": candidate_limit,
        "rrf_k": 60,
        "file_path_prefix": file_path_prefix,
        "language": language,
        "commit_sha": commit_sha,
        "cursor_score": cursor_score,
        "cursor_id": cursor_id,
        "limit_plus_one": top_k + 1,
    }

    rows = db.execute(stmt, params).mappings().all()
    has_more = len(rows) > top_k
    page_rows = rows[:top_k]

    items = [
        RetrievedSnippet(
            id=str(row["id"]),
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            commit_sha=row["commit_sha"],
            score=round(float(row["score"]), 6),
            chunk_text=row["chunk_text"],
        )
        for row in page_rows
    ]
    next_cursor = _encode_cursor(items[-1].score, items[-1].id) if has_more and items else None
    return RetrievalPage(items=items, next_cursor=next_cursor)
