from __future__ import annotations

import argparse
import statistics
import time
from uuid import UUID

from app.db.engine import SessionLocal
from app.embeddings.router import get_embedding_provider
from app.retrieval.hybrid import hybrid_search, hybrid_search_legacy


def _timed_ms(fn):
    start = time.perf_counter()
    result = fn()
    elapsed = (time.perf_counter() - start) * 1000
    return result, elapsed


def benchmark(project_id: UUID, query: str, top_k: int, runs: int) -> None:
    embedder = get_embedding_provider()
    query_embedding = embedder.embed([query])[0]

    new_latencies: list[float] = []
    old_latencies: list[float] = []
    overlaps: list[float] = []

    with SessionLocal() as db:
        for _ in range(runs):
            old_rows, old_ms = _timed_ms(
                lambda: hybrid_search_legacy(
                    db=db,
                    project_id=project_id,
                    query=query,
                    query_embedding=query_embedding,
                    top_k=top_k,
                )
            )
            new_page, new_ms = _timed_ms(
                lambda: hybrid_search(
                    db=db,
                    project_id=project_id,
                    query=query,
                    query_embedding=query_embedding,
                    top_k=top_k,
                )
            )

            old_ids = {row.id for row in old_rows}
            new_ids = {row.id for row in new_page.items}
            overlap = len(old_ids.intersection(new_ids)) / max(1, min(len(old_ids), len(new_ids)))

            old_latencies.append(old_ms)
            new_latencies.append(new_ms)
            overlaps.append(overlap)

    print("=== Retrieval Benchmark ===")
    print(f"Project: {project_id}")
    print(f"Query: {query}")
    print(f"Runs: {runs}")
    print(f"Top-K: {top_k}")
    print(f"Legacy latency (ms): avg={statistics.mean(old_latencies):.2f} p95={statistics.quantiles(old_latencies, n=20)[-1]:.2f}")
    print(f"SQL latency (ms):    avg={statistics.mean(new_latencies):.2f} p95={statistics.quantiles(new_latencies, n=20)[-1]:.2f}")
    print(f"Top-K overlap:       avg={statistics.mean(overlaps):.3f} min={min(overlaps):.3f} max={max(overlaps):.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare legacy and SQL hybrid retrieval latency/overlap")
    parser.add_argument("project_id", type=UUID)
    parser.add_argument("query", type=str)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()

    benchmark(project_id=args.project_id, query=args.query, top_k=args.top_k, runs=args.runs)
