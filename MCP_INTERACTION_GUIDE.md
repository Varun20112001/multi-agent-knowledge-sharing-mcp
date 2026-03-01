# MCP Interaction Guide

This document explains how to interact with the `multi-agent-local` MCP server from VS Code agents.

## MCP Status (Verified)

A real stdio MCP client session was executed locally against this server and verified:

- Server initialized: `multi-agent-knowledge-sharing` (MCP SDK `1.26.0`)
- Tools discovered: `9`
- `ensure_project` returned existing `project_id` for `idi-ap-be`
- `list_project_files` returned indexed files for `assessment_individual_report/*`

## 1. Start Services

From repo root (`d:\PRO\multi-agent-knowledge-sharing-mcp`):

```powershell
uv venv .venv
.\.venv\Scripts\activate
uv sync
uv run alembic upgrade head
uv run python -m app.main
```

In another terminal, run MCP server (stdio mode for VS Code):

```powershell
uv run python -m app.run_mcp
```

## 2. VS Code MCP Config

Use user config: `C:\Users\kanad\AppData\Roaming\Code\User\mcp.json`

```json
{
  "servers": {
    "multi-agent-local": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "-m", "app.run_mcp"],
      "cwd": "d:\\PRO\\multi-agent-knowledge-sharing-mcp"
    }
  }
}
```

Then run `MCP: Restart Servers` in VS Code.

## 3. Tool Workflow (Recommended)

Always use this order:

1. `ensure_project`
2. `ingest_repository` (returns immediately in async mode)
3. `get_ingestion_status` until status is `success`
4. `list_project_files` (optional but recommended)
5. `validate_citations`
6. `store_memory`
7. `search_memory` / `search_docs` / `verify_memory`

## 3.1 Internal Code Map

If you need to debug or extend behavior, these are the core files:

- MCP tool registration: [app/mcp_server.py](d:/PRO/multi-agent-knowledge-sharing-mcp/app/mcp_server.py)
- Memory validation + citation resolution: [app/memory/service.py](d:/PRO/multi-agent-knowledge-sharing-mcp/app/memory/service.py)
- Ingestion orchestration: [app/ingestion/service.py](d:/PRO/multi-agent-knowledge-sharing-mcp/app/ingestion/service.py)
- File loading/exclusion rules: [app/ingestion/loader.py](d:/PRO/multi-agent-knowledge-sharing-mcp/app/ingestion/loader.py)
- DB models: [app/db/models.py](d:/PRO/multi-agent-knowledge-sharing-mcp/app/db/models.py)
- API app + tool listing endpoint: [app/main.py](d:/PRO/multi-agent-knowledge-sharing-mcp/app/main.py)

## 4. Tool Reference

### `ensure_project`
Create or reuse a project by repository name.

Input:

```json
{
  "repo_name": "multi-agent-knowledge-sharing-mcp"
}
```

Output:

```json
{
  "project_id": "<uuid>",
  "project_name": "multi-agent-knowledge-sharing-mcp",
  "created": true
}
```

### `ingest_repository`
Index repository files so retrieval and citation validation can work.

Input:

```json
{
  "project_id": "<uuid>",
  "repo_path": "d:/PRO/multi-agent-knowledge-sharing-mcp",
  "wait_for_completion": false
}
```

`wait_for_completion` defaults to `false` to avoid MCP chat timeouts on large repositories.

### `get_ingestion_status`
Check progress/result of async ingestion.

Input:

```json
{
  "ingestion_run_id": "<uuid>"
}
```

### `list_project_files`
List indexed file paths for reliable citation references.

Input:

```json
{
  "project_id": "<uuid>",
  "prefix": "orders/apis",
  "limit": 200
}
```

### `validate_citations`
Preflight check citations before storing memory.

Input:

```json
{
  "project_id": "<uuid>",
  "citations": [
    {
      "file_path": "orders/apis/assessment_individual_report/create_report.py",
      "line_start": 1,
      "line_end": 40
    }
  ]
}
```

Compatibility input is accepted:

```json
{
  "url": "orders/apis/assessment_individual_report/create_report.py",
  "line_start": 1
}
```

### `store_memory`
Store structured memory with validated citations.

Input:

```json
{
  "project_id": "<uuid>",
  "subject": "create_report flow",
  "fact": "create_report.py orchestrates PDF generation...",
  "reason": "document flow and key components",
  "confidence": 0.9,
  "citations": [
    {
      "file_path": "orders/apis/assessment_individual_report/create_report.py",
      "line_start": 1,
      "line_end": 40
    }
  ]
}
```

### `search_docs`
Retrieve code/document snippets by semantic + keyword relevance.

Input:

```json
{
  "project_id": "<uuid>",
  "query": "how create_report builds pdf",
  "top_k": 8
}
```

### `search_memory`
Retrieve stored project memories.

Input:

```json
{
  "project_id": "<uuid>",
  "query": "create_report flow",
  "top_k": 5,
  "include_stale": false
}
```

### `verify_memory`
Re-check stored memory citations against indexed code.

Input:

```json
{
  "project_id": "<uuid>",
  "memory_id": "<memory_uuid>"
}
```

## 5. Error Codes You May See

- `INVALID_SCHEMA`: Missing/invalid required fields.
- `PROJECT_NOT_INGESTED`: No indexed chunks found; run `ingest_repository`.
- `CITATION_PATH_NOT_FOUND`: Citation path not found in indexed files.
- `CITATION_LINE_OUT_OF_RANGE`: File exists, but cited line does not map to indexed chunk lines.

## 6. Common Fixes

- If path resolution fails:
  - Call `list_project_files` and copy exact `file_path`.
  - Use forward slashes `/`.
- If store fails due to citations:
  - Run `validate_citations` first.
- If no retrieval results:
  - Re-run `ingest_repository` for the correct `project_id` and `repo_path`.
- If chat times out during ingestion:
  - Keep `wait_for_completion=false` (default).
  - Poll with `get_ingestion_status`.

## 7. Quick Agent Prompt Template

Use this in Agent mode:

```text
1) Ensure project for repo "<repo_name>"
2) Ingest repo at "<absolute_path>"
3) Validate citations:
   - <file_path>:<line_start>-<line_end>
4) Store memory with subject "<subject>" and fact "<fact>"
5) Search memory for "<query>"
```

## 8. Local MCP Smoke Test (CLI)

Run this from repo root to verify stdio MCP connection and tool availability:

```powershell
@'
import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    server = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "app.run_mcp"],
        cwd=r"d:\PRO\multi-agent-knowledge-sharing-mcp",
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

anyio.run(main)
'@ | uv run python -
```
