# Multi-Agent Knowledge Sharing MCP

FastAPI + FastMCP backend for project-scoped RAG and memory sharing across coding agents.

## Stack

- Python 3.12+
- FastAPI
- FastMCP (`mcp` package)
- PostgreSQL 16 + pgvector
- SQLAlchemy + Alembic
- `uv` for dependency management

## Quickstart (Windows)

```powershell
uv venv .venv
.\.venv\Scripts\activate
uv sync
Copy-Item .env.example .env
# Ensure local PostgreSQL is running with:
# DB_NAME=multi_agent
# DB_USER=postgres
# DB_PASSWORD=postgres
# DB_HOST=localhost
# DB_PORT=5432
uv run alembic upgrade head
uv run python scripts/seed_local.py
uv run python -m app.main
```

## API

- `GET /healthz`
- `POST /v1/ingest/repo`
- `GET /v1/ingest/{ingestion_run_id}`
- `POST /v1/memory/verify`
- `GET /v1/mcp/info`

## FastMCP tools

- `ensure_project`
- `ingest_repository`
- `get_ingestion_status`
- `list_project_files`
- `validate_citations`
- `search_docs`
- `store_memory`
- `search_memory`
- `verify_memory`

## Developer commands

```powershell
uv run pytest
uv run alembic upgrade head
uv run python -m app.main
```

## Notes

- `EMBED_PROVIDER=local` uses deterministic fallback embeddings (no API key required).
- Set `EMBED_PROVIDER=openai` and `OPENAI_API_KEY` for real embeddings.
- Keep `EMBEDDING_DIM` at `<=2000` when using HNSW indexes with `pgvector` (default is `1536`).
- LLM provider routing is implemented (`openai`, `anthropic`, `local` stub).

## Memory Citation Schema

Use this citation shape when calling `store_memory`:

```json
{
  "file_path": "orders/apis/assessment_individual_report/create_report.py",
  "line_start": 1,
  "line_end": 40,
  "quote": "optional"
}
```

Compatibility input (`url`) is accepted and normalized to `file_path`, but canonical storage always uses `file_path`.

Recommended tool sequence:
1. `ensure_project`
2. `ingest_repository` (async default)
3. `get_ingestion_status` until `status=success`
4. `list_project_files` / `validate_citations`
5. `store_memory`
