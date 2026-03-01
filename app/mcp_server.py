from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from sqlalchemy import select

from app.db.engine import SessionLocal
from app.db.models import IngestionRun, Memory, Project
from app.embeddings.router import get_embedding_provider
from app.ingestion.service import create_ingestion_run, execute_ingestion_run, ingest_repository
from app.memory.service import (
    MemoryValidationError,
    list_project_files,
    search_memory,
    store_memory,
    validate_citations,
    verify_memory,
)
from app.retrieval.hybrid import hybrid_search
from app.schemas.api import CitationInput, IngestRepoRequest, SearchMemoryRequest, StoreMemoryRequest

_INGESTION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-ingestion")


def _run_ingestion_in_background(payload_dict: dict[str, object], run_id: str) -> None:
    payload = IngestRepoRequest(**payload_dict)
    with SessionLocal() as db:
        execute_ingestion_run(db, UUID(run_id), payload, get_embedding_provider())
        db.commit()


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
    def ensure_project(
        repo_name: Annotated[
            str,
            Field(
                description=(
                    "Repository identifier (name/path). Used as idempotent project key."
                )
            ),
        ]
    ) -> dict[str, object]:
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
        name="ingest_repository",
        description="Index a local repository for a project so citations and semantic retrieval can resolve paths and lines.",
    )
    def ingest_repository_tool(
        project_id: Annotated[str, Field(description="Project UUID string")],
        repo_path: Annotated[str, Field(description="Local absolute path of the repository to ingest")],
        include_globs: Annotated[
            list[str], Field(description="Glob patterns to include")
        ] = ["**/*"],
        exclude_globs: Annotated[
            list[str],
            Field(description="Glob patterns to exclude"),
        ] = ["**/.git/**", "**/.venv/**", "**/__pycache__/**"],
        max_file_size_kb: Annotated[
            int, Field(description="Maximum file size to ingest in KB", ge=1, le=10240)
        ] = 512,
        wait_for_completion: Annotated[
            bool,
            Field(
                description=(
                    "When true, run ingestion synchronously. Default false to avoid MCP timeout; poll using get_ingestion_status."
                )
            ),
        ] = False,
    ) -> dict[str, object]:
        payload = IngestRepoRequest(
            project_id=UUID(project_id),
            repo_path=repo_path,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            max_file_size_kb=max_file_size_kb,
        )

        with SessionLocal() as db:
            if wait_for_completion:
                run = ingest_repository(db, payload, get_embedding_provider())
                db.commit()
                return {
                    "ingestion_run_id": str(run.id),
                    "status": run.status,
                    "files_scanned": run.files_scanned,
                    "chunks_written": run.chunks_written,
                    "head_commit_sha": run.head_commit_sha,
                    "mode": "sync",
                    "error": run.error,
                }

            run = create_ingestion_run(db, payload)
            db.commit()
            _INGESTION_EXECUTOR.submit(
                _run_ingestion_in_background,
                payload.model_dump(mode="json"),
                str(run.id),
            )
            return {
                "ingestion_run_id": str(run.id),
                "status": "running",
                "files_scanned": 0,
                "chunks_written": 0,
                "head_commit_sha": run.head_commit_sha,
                "mode": "async",
            }

    @mcp.tool(
        name="get_ingestion_status",
        description="Get status and progress for an ingestion run. Use this to poll async ingestion completion.",
    )
    def get_ingestion_status_tool(
        ingestion_run_id: Annotated[str, Field(description="Ingestion run UUID string")],
    ) -> dict[str, object]:
        with SessionLocal() as db:
            run = db.execute(select(IngestionRun).where(IngestionRun.id == UUID(ingestion_run_id))).scalar_one_or_none()
            if run is None:
                return {"status": "error", "error": "Ingestion run not found"}
            return {
                "ingestion_run_id": str(run.id),
                "project_id": str(run.project_id),
                "status": run.status,
                "files_scanned": run.files_scanned,
                "chunks_written": run.chunks_written,
                "head_commit_sha": run.head_commit_sha,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error": run.error,
            }

    @mcp.tool(
        name="list_project_files",
        description="List indexed repository file paths for a project to help build valid citation file_path values.",
    )
    def list_project_files_tool(
        project_id: Annotated[str, Field(description="Project UUID string")],
        prefix: Annotated[str | None, Field(description="Optional file path prefix filter")] = None,
        limit: Annotated[int, Field(description="Max number of paths returned", ge=1, le=2000)] = 200,
    ) -> dict[str, object]:
        with SessionLocal() as db:
            files = list_project_files(db, UUID(project_id), prefix=prefix, limit=limit)
            return {"files": files, "count": len(files)}

    @mcp.tool(
        name="validate_citations",
        description="Preflight-check citation objects and return normalized/resolved citation paths before calling store_memory.",
    )
    def validate_citations_tool(
        project_id: Annotated[str, Field(description="Project UUID string")],
        citations: Annotated[
            list[CitationInput],
            Field(description="Citation objects with file_path or url, plus line_start/line_end"),
        ],
    ) -> dict[str, object]:
        with SessionLocal() as db:
            return validate_citations(db, UUID(project_id), citations)

    @mcp.tool(
        name="search_docs",
        description="Semantic + keyword hybrid retrieval over indexed repository chunks. Returns top matching snippets with file and line metadata.",
    )
    def search_docs(
        project_id: Annotated[str, Field(description="Project UUID string")],
        query: Annotated[str, Field(description="Search query for code/docs retrieval")],
        top_k: Annotated[int, Field(description="Number of snippets to return", ge=1, le=50)] = 8,
        file_path_prefix: Annotated[
            str | None, Field(description="Optional file path prefix filter")
        ] = None,
        language: Annotated[str | None, Field(description="Optional language filter")] = None,
        commit_sha: Annotated[str | None, Field(description="Optional commit SHA filter")] = None,
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
        project_id: Annotated[str, Field(description="Project UUID string")],
        subject: Annotated[str, Field(description="Short memory subject")],
        fact: Annotated[str, Field(description="The factual memory text to store")],
        citations: Annotated[
            list[CitationInput],
            Field(
                description=(
                    "Required evidence list. Each item should include file_path "
                    "(or url compatibility alias) and line_start."
                ),
                min_length=1,
            ),
        ],
        reason: Annotated[str | None, Field(description="Optional rationale for the fact")] = None,
        confidence: Annotated[
            float, Field(description="Confidence score between 0 and 1", ge=0, le=1)
        ] = 0.75,
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
                return {
                    "status": "rejected",
                    "error": exc.message,
                    "error_code": exc.code,
                    "details": exc.details,
                    "expected_citation_shape": {
                        "file_path": "repo/relative/path.py",
                        "line_start": 1,
                        "line_end": 10,
                        "quote": "optional",
                    },
                }

    @mcp.tool(
        name="search_memory",
        description="Search previously stored project memories using hybrid lexical/vector similarity, optionally including stale entries.",
    )
    def search_memory_tool(
        project_id: Annotated[str, Field(description="Project UUID string")],
        query: Annotated[str, Field(description="Memory search query")],
        top_k: Annotated[int, Field(description="Number of memories to return", ge=1, le=50)] = 5,
        include_stale: Annotated[
            bool, Field(description="Include stale/superseded memories when true")
        ] = False,
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
    def verify_memory_tool(
        project_id: Annotated[str, Field(description="Project UUID string")],
        memory_id: Annotated[str, Field(description="Memory UUID string")],
    ) -> dict[str, object]:
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
