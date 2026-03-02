import logging
import time
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.provider_router import get_llm_provider
from app.config import get_settings
from app.db.engine import get_db
from app.db.models import IngestionRun
from app.embeddings.router import get_embedding_provider
from app.ingestion.service import ingest_repository
from app.mcp_server import register_mcp_tools
from app.memory.service import search_memory, verify_memory
from app.rag.orchestrator import MemoryService, RAGOrchestrator, Retriever
from app.retrieval.hybrid import RetrievedSnippet, hybrid_search
from app.schemas.api import (
    IngestRepoRequest,
    IngestRepoResponse,
    IngestionStatusResponse,
    RunRAGRequest,
    RunRAGResponse,
    SnippetResponse,
    VerifyMemoryRequest,
    VerifyMemoryResponse,
)
from app.security.api_keys import require_api_key, verify_api_key

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

settings = get_settings()
app = FastAPI(title=settings.app_name)
mcp = register_mcp_tools()


class DBRetriever(Retriever):
    def __init__(self, db: Session) -> None:
        self.db = db
        self.embedder = get_embedding_provider()

    def retrieve(self, *, project_id: UUID, query: str, top_k: int) -> list[RetrievedSnippet]:
        query_embedding = self.embedder.embed([query])[0]
        page = hybrid_search(self.db, project_id, query, query_embedding, top_k)
        return page.items


class DBMemoryService(MemoryService):
    def __init__(self, db: Session) -> None:
        self.db = db
        self.embedder = get_embedding_provider()

    def recall(self, *, project_id: UUID, query: str, top_k: int) -> list[dict[str, object]]:
        query_embedding = self.embedder.embed([query])[0]
        rows = search_memory(self.db, project_id, query, query_embedding, top_k, include_stale=False)
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


@app.on_event("startup")
def startup_event() -> None:
    get_embedding_provider()


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@app.post("/v1/ingest/repo", response_model=IngestRepoResponse)
def ingest_repo(
    payload: IngestRepoRequest,
    api_key: str = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> IngestRepoResponse:
    start = time.perf_counter()
    key = verify_api_key(db, api_key)
    if key.project_id != payload.project_id:
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")

    run = ingest_repository(db, payload, get_embedding_provider())
    db.commit()

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("ingest_repo completed in %.2fms", elapsed_ms)
    return IngestRepoResponse(ingestion_run_id=run.id, status=run.status)


@app.get("/v1/ingest/{ingestion_run_id}", response_model=IngestionStatusResponse)
def ingest_status(
    ingestion_run_id: UUID,
    api_key: str = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> IngestionStatusResponse:
    verify_api_key(db, api_key)
    run = db.execute(select(IngestionRun).where(IngestionRun.id == ingestion_run_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found")
    return IngestionStatusResponse(
        ingestion_run_id=run.id,
        status=run.status,
        files_scanned=run.files_scanned,
        chunks_written=run.chunks_written,
        files_changed=run.files_changed,
        chunks_upserted=run.chunks_upserted,
        chunks_deactivated=run.chunks_deactivated,
        error=run.error,
    )


@app.post("/v1/memory/verify", response_model=VerifyMemoryResponse)
def verify_memory_endpoint(
    payload: VerifyMemoryRequest,
    api_key: str = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> VerifyMemoryResponse:
    key = verify_api_key(db, api_key)
    if key.project_id != payload.project_id:
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")

    before, after, checks = verify_memory(db, payload.project_id, payload.memory_id)
    db.commit()
    return VerifyMemoryResponse(status_before=before, status_after=after, checks=checks)


@app.post("/v1/rag/run", response_model=RunRAGResponse)
def run_rag(
    payload: RunRAGRequest,
    api_key: str = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> RunRAGResponse:
    key = verify_api_key(db, api_key)
    if key.project_id != payload.project_id:
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")

    providers = {}
    for provider_name in settings.provider_execution.provider_priority:
        try:
            providers[provider_name] = get_llm_provider(provider_name)
        except Exception as exc:  # pragma: no cover
            logger.warning("Skipping provider %s: %s", provider_name, exc)

    orchestrator = RAGOrchestrator(
        retriever=DBRetriever(db),
        memory_service=DBMemoryService(db),
        providers=providers,
        execution_config=settings.provider_execution,
    )
    result = orchestrator.run(project_id=payload.project_id, query=payload.query, top_k=payload.top_k)
    return RunRAGResponse(
        answer=result.answer,
        provider=result.provider,
        model=result.model,
        snippets=[SnippetResponse(**snippet.__dict__) for snippet in result.snippets],
        memories=result.memories,
        telemetry=[attempt.__dict__ for attempt in result.telemetry],
    )


@app.get("/v1/mcp/info")
def mcp_info() -> dict[str, object]:
    return {
        "server": "fastmcp",
        "tools": [
            "ensure_project",
            "ingest_repository",
            "get_ingestion_status",
            "list_project_files",
            "validate_citations",
            "search_docs",
            "store_memory",
            "search_memory",
            "verify_memory",
            "run_rag",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)
