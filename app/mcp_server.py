from __future__ import annotations

from uuid import UUID

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.db.engine import SessionLocal
from app.db.models import IngestionRun, Memory, Project
from app.embeddings.router import get_embedding_provider
from app.ingestion.loader import get_head_commit_sha
from app.memory.service import MemoryValidationError, search_memory, store_memory, verify_memory
from app.retrieval.hybrid import hybrid_search
from app.schemas.api import SearchMemoryRequest, StoreMemoryRequest


def register_mcp_tools(
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
    streamable_http_path: str = "/mcp",
) -> FastMCP:
    mcp = FastMCP(
        "multi-agent-knowledge-sharing",
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
    )

    @mcp.tool(
        name="ensure_project",
        description="Create (or reuse) a project record from a repository name and return its project_id. Idempotent for the same normalized name.",
    )
    def ensure_project(repo_name: str) -> dict[str, object]:
        normalized_name = repo_name.strip().replace("\\", "/").rstrip("/")
        if not normalized_name:
            return {"status": "error", "error": "repo_name must not be empty"}

        with SessionLocal() as db:
            existing = db.execute(
                select(Project).where(Project.name == normalized_name)
            ).scalar_one_or_none()
            if existing is not None:
                return {
                    "project_id": str(existing.id),
                    "project_name": existing.name,
                    "created": False,
                }

            project = Project(name=normalized_name)
            db.add(project)
            db.commit()
            db.refresh(project)
            return {
                "project_id": str(project.id),
                "project_name": project.name,
                "created": True,
            }

    @mcp.tool(
        name="search_docs",
        description="Semantic + keyword hybrid retrieval over indexed repository chunks. Returns top matching snippets with file and line metadata.",
    )
    def search_docs(
        project_id: str,
        query: str,
        top_k: int = 8,
        file_path_prefix: str | None = None,
        language: str | None = None,
        commit_sha: str | None = None,
    ) -> list[dict[str, object]]:
        pid = UUID(project_id)
        embedder = get_embedding_provider()
        query_embedding = embedder.embed([query])[0]

        with SessionLocal() as db:
            rows = hybrid_search(
                db=db,
                project_id=pid,
                query=query,
                query_embedding=query_embedding,
                top_k=top_k,
                file_path_prefix=file_path_prefix,
                language=language,
                commit_sha=commit_sha,
            )
            return [row.__dict__ for row in rows]

    @mcp.tool(
        name="store_memory",
        description="Store a structured project memory (subject/fact/reason/citations) after validating citations against indexed repository chunks.",
    )
    def store_memory_tool(
        project_id: str,
        subject: str,
        fact: str,
        citations: list[dict[str, object]],
        reason: str | None = None,
        confidence: float = 0.75,
    ) -> dict[str, object]:
        pid = UUID(project_id)
        payload = StoreMemoryRequest(
            project_id=pid,
            subject=subject,
            fact=fact,
            reason=reason,
            citations=citations,
            confidence=confidence,
        )
        embedder = get_embedding_provider()

        with SessionLocal() as db:
            try:
                latest_run = db.execute(
                    select(IngestionRun)
                    .where(IngestionRun.project_id == pid)
                    .order_by(IngestionRun.started_at.desc())
                ).scalar_one_or_none()
                source_commit_sha = latest_run.head_commit_sha if latest_run else "unknown"
                memory = store_memory(db, payload, embedder, source_commit_sha=source_commit_sha)
                db.commit()
                return {"memory_id": str(memory.id), "status": memory.status}
            except MemoryValidationError as exc:
                db.rollback()
                return {"status": "rejected", "error": str(exc)}

    @mcp.tool(
        name="search_memory",
        description="Search previously stored project memories using hybrid lexical/vector similarity, optionally including stale entries.",
    )
    def search_memory_tool(
        project_id: str,
        query: str,
        top_k: int = 5,
        include_stale: bool = False,
    ) -> list[dict[str, object]]:
        payload = SearchMemoryRequest(
            project_id=UUID(project_id), query=query, top_k=top_k, include_stale=include_stale
        )
        embedder = get_embedding_provider()
        query_embedding = embedder.embed([query])[0]

        with SessionLocal() as db:
            rows = search_memory(
                db,
                project_id=payload.project_id,
                query=payload.query,
                query_embedding=query_embedding,
                top_k=payload.top_k,
                include_stale=payload.include_stale,
            )
            return [
                {
                    "memory_id": str(mem.id),
                    "subject": mem.subject,
                    "fact": mem.fact,
                    "citations": mem.citations,
                    "confidence": float(mem.confidence),
                    "status": mem.status,
                }
                for mem in rows
            ]

    @mcp.tool(
        name="verify_memory",
        description="Re-validate a stored memory's citations against current indexed chunks and update status (for example active to stale).",
    )
    def verify_memory_tool(project_id: str, memory_id: str) -> dict[str, object]:
        with SessionLocal() as db:
            before, after, checks = verify_memory(db, UUID(project_id), UUID(memory_id))
            db.commit()
            memory = db.execute(select(Memory).where(Memory.id == UUID(memory_id))).scalar_one()
            return {
                "memory_id": str(memory.id),
                "status_before": before,
                "status_after": after,
                "checks": checks,
            }

    return mcp
