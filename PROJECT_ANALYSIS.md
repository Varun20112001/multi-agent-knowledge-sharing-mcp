# Project Analysis: Multi-Agent Knowledge Sharing MCP

## 1) Overview

`multi-agent-knowledge-sharing-mcp` is a backend platform for **project-scoped retrieval augmented generation (RAG)** and **shared memory** across coding agents.

It combines:

- FastAPI REST endpoints
- FastMCP tool surface for agent-native workflows
- PostgreSQL + pgvector for persistence and vector search
- SQLAlchemy + Alembic for schema and migrations

Primary objective:

- Enable multiple agents to ingest a codebase once, retrieve relevant snippets semantically, and store citation-backed memories that can be re-verified as code evolves.

## 2) Problem It Solves

Without a shared memory and retrieval layer, each coding agent has to repeatedly rediscover context from large repositories.

This system addresses:

- **Knowledge fragmentation**: memories are centralized per project.
- **Trust and grounding**: stored memories require file+line citations resolvable to indexed chunks.
- **Repository-scale search**: code/doc chunks are embedded and searchable through hybrid lexical+vector ranking.
- **Agent workflow compatibility**: exposed through MCP tools with an explicit recommended sequence.

## 3) Tech Stack

- Python 3.12+
- FastAPI
- FastMCP (`mcp` package)
- PostgreSQL 16
- pgvector
- SQLAlchemy 2.x
- Alembic
- OpenAI embeddings (with deterministic local fallback)

## 4) High-Level Architecture

```text
+---------------------+     +-------------------------+
| MCP Clients/Agents  |     | REST API Clients        |
+----------+----------+     +-----------+-------------+
           |                            |
           +------------+---------------+
                        v
             +----------+-----------+
             | FastAPI + FastMCP    |
             | app/main.py          |
             | app/mcp_server.py    |
             +----------+-----------+
                        |
      +-----------------+--------------------+
      |                 |                    |
      v                 v                    v
+-----+------+   +------+--------+   +-------+--------+
| Ingestion  |   | Retrieval     |   | Memory Service |
| service    |   | hybrid search |   | citations/verify|
+-----+------+   +------+--------+   +-------+--------+
      \                 |                    /
       \                |                   /
        +---------------+------------------+
                        v
               +--------+--------+
               | Postgres+pgvector|
               | projects, chunks |
               | memories, runs   |
               +------------------+
```

## 5) Codebase Structure and Responsibilities

### Entry points

- `app/main.py`: REST app bootstrapping and endpoints.
- `app/run_mcp.py`: MCP server startup (stdio/http transport driven by env).

### MCP tools

Defined in `app/mcp_server.py`:

- `ensure_project`
- `ingest_repository`
- `get_ingestion_status`
- `list_project_files`
- `validate_citations`
- `search_docs`
- `store_memory`
- `search_memory`
- `verify_memory`

### Ingestion

- `app/ingestion/loader.py`: repository file loading with exclusion/size/binary filters.
- `app/ingestion/chunker.py`: line-window chunking.
- `app/ingestion/service.py`: orchestration of chunk generation, embedding, and DB writes.

### Retrieval

- `app/retrieval/hybrid.py`: lexical + cosine fusion ranking (RRF-style approach in Python).

### Memory management

- `app/memory/service.py`: citation normalization, validation, store/search/verify lifecycle.

### Config/Auth/Providers

- `app/config.py`: env-driven settings.
- `app/security/api_keys.py`: API key validation.
- `app/embeddings/router.py`: embedding provider selection (`openai`/`local`).
- `app/agents/provider_router.py`: LLM provider abstraction (`openai`, `anthropic`, `local` stub).

### Data layer

- `app/db/models.py`: ORM models.
- `app/db/migrations/versions/0001_initial.py`: initial schema and indexes.

## 6) End-to-End Flows

## A) Ingestion flow

```text
ensure_project
  -> ingest_repository
      -> create ingestion_runs row
      -> scan files by globs and filters
      -> chunk each file by lines
      -> embed all chunks
      -> replace repo_chunks for that repo/project
      -> mark run success/failed
  -> get_ingestion_status (for async mode)
```

Notable behavior:

- Async ingestion is implemented via a thread pool in MCP server.
- Commit SHA is captured via `git rev-parse HEAD` when available.

## B) Retrieval flow (`search_docs`)

```text
query
 -> embed query
 -> fetch candidate chunks (project + optional filters)
 -> rank by vector similarity and lexical overlap
 -> fuse ranks (RRF)
 -> return top snippets with file + line metadata
```

## C) Memory flow (`store_memory` and `verify_memory`)

```text
store_memory
 -> validate citations against indexed files/chunks
 -> normalize paths
 -> resolve line coverage
 -> embed memory text
 -> write memory row (active)
 -> supersede prior active memory with same subject if fact changed

verify_memory
 -> re-check each citation path + line against current chunks
 -> mark stale when citations no longer resolve
```

## 7) Database Schema Analysis

Core tables:

- `projects`: project tenancy boundary.
- `api_keys`: auth credentials scoped to project.
- `repo_chunks`: indexed chunks with line ranges, language, commit, vector, tsvector.
- `memories`: structured memory records with citations and confidence.
- `ingestion_runs`: ingestion telemetry and status.

Indexing highlights:

- B-tree indexes on common project/status/file filters.
- GIN index for `repo_chunks.tsv` full-text support.
- HNSW vector indexes for chunk/memory embedding similarity.

Constraints:

- Memory status constrained to: `active`, `stale`, `superseded`, `rejected`.
- Ingestion status constrained to: `running`, `success`, `failed`.
- Unique ingestion constraint on `(project_id, head_commit_sha)`.

## 8) Security and Access Control

- API key required for protected REST endpoints.
- API keys are project-scoped; ingest/verify endpoints validate scope against payload project.
- Key validation currently checks active keys and compares hashes in application code.

## 9) Strengths

- Clear separation of concerns across ingestion/retrieval/memory layers.
- MCP + REST dual interface is practical for both agents and services.
- Citation validation before memory storage improves trust.
- Async ingestion path helps avoid long-running tool timeouts.
- Local deterministic embedding fallback improves developer ergonomics.

## 10) Current Architectural Gaps (Production Scale View)

1. Ingestion strategy currently rewrites chunk corpus per repo/project instead of incremental upserts.
2. Retrieval ranking is application-side for candidate set, not DB-native hybrid ranking.
3. API key verification pattern is not optimized for large active key counts.
4. Memory verification is line-resolution based and does not detect semantic drift.
5. LLM provider router exists but is not wired into an end-to-end RAG orchestration layer.

## 11) Recommended Evolution Path

1. Move hybrid ranking into SQL (pgvector + FTS fusion) with keyset pagination.
2. Add diff-based incremental ingestion using chunk hashes and idempotent upserts.
3. Redesign API key format for indexed key-id lookup + secure hash verification.
4. Add background semantic memory re-verification with confidence scoring.
5. Introduce a RAG orchestrator layer that uses provider routing with failover and telemetry.

## 12) Operational Considerations

- Add observability:
  - ingestion throughput and queue latency
  - retrieval P50/P95 latency
  - embedding and LLM provider cost/usage
  - memory verification backlog
- Strengthen reliability:
  - per-project ingestion locks
  - retry strategy and dead-letter handling for background jobs
  - migration-safe feature flags for architectural transitions

## 13) Conclusion

This codebase is a strong V1 for multi-agent MCP + RAG memory sharing.

It already includes the key primitives required for production systems (project scoping, citation-grounded memory, pgvector storage, async ingestion). With incremental ingestion, SQL-native retrieval fusion, scaled auth lookup, semantic memory drift detection, and orchestrated LLM routing, it can mature into a robust platform capable of handling very large repositories and sustained multi-agent traffic.
