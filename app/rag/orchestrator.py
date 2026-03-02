from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from app.agents.provider_router import (
    LLMProvider,
    NonRetryableProviderError,
    NormalizedLLMResponse,
    ProviderError,
    RetryableProviderError,
)
from app.config import ProviderExecutionConfig
from app.retrieval.hybrid import RetrievedSnippet


class Retriever(Protocol):
    def retrieve(
        self,
        *,
        project_id: UUID,
        query: str,
        top_k: int,
    ) -> list[RetrievedSnippet]: ...


class MemoryService(Protocol):
    def recall(
        self,
        *,
        project_id: UUID,
        query: str,
        top_k: int,
    ) -> list[dict[str, object]]: ...


@dataclass
class ProviderAttemptTelemetry:
    provider: str
    model: str
    attempt: int
    latency_ms: float
    outcome: str
    error_type: str | None = None
    error_message: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class OrchestratorResponse:
    answer: str
    provider: str
    model: str
    snippets: list[RetrievedSnippet]
    memories: list[dict[str, object]]
    telemetry: list[ProviderAttemptTelemetry] = field(default_factory=list)


@dataclass
class _CircuitState:
    failures: int = 0
    opened_until: datetime | None = None


class RAGOrchestrator:
    def __init__(
        self,
        *,
        retriever: Retriever,
        memory_service: MemoryService,
        providers: dict[str, LLMProvider],
        execution_config: ProviderExecutionConfig,
    ) -> None:
        self.retriever = retriever
        self.memory_service = memory_service
        self.providers = providers
        self.execution_config = execution_config
        self._circuit_breakers: dict[str, _CircuitState] = {
            name: _CircuitState() for name in providers
        }

    def run(self, *, project_id: UUID, query: str, top_k: int = 8) -> OrchestratorResponse:
        snippets = self.retriever.retrieve(project_id=project_id, query=query, top_k=top_k)
        memories = self.memory_service.recall(project_id=project_id, query=query, top_k=max(3, top_k // 2))
        prompt_messages = self._build_prompt(query=query, snippets=snippets, memories=memories)

        telemetry: list[ProviderAttemptTelemetry] = []

        for provider_name in self.execution_config.provider_priority:
            provider = self.providers.get(provider_name)
            if provider is None:
                continue
            if self._is_circuit_open(provider_name):
                telemetry.append(
                    ProviderAttemptTelemetry(
                        provider=provider_name,
                        model="n/a",
                        attempt=0,
                        latency_ms=0,
                        outcome="circuit_open",
                    )
                )
                continue

            response = self._run_provider_with_retries(provider, prompt_messages, telemetry)
            if response:
                self._record_success(provider_name)
                return OrchestratorResponse(
                    answer=response.content,
                    provider=response.provider,
                    model=response.model,
                    snippets=snippets,
                    memories=memories,
                    telemetry=telemetry,
                )

        raise RuntimeError("All providers failed to produce a response")

    def _run_provider_with_retries(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        telemetry: list[ProviderAttemptTelemetry],
    ) -> NormalizedLLMResponse | None:
        max_attempts = self.execution_config.retry_limit + 1
        for attempt in range(1, max_attempts + 1):
            start = time.perf_counter()
            try:
                response = provider.generate(
                    messages=messages,
                    timeout_s=self.execution_config.timeout_seconds,
                )
                telemetry.append(
                    ProviderAttemptTelemetry(
                        provider=response.provider,
                        model=response.model,
                        attempt=attempt,
                        latency_ms=response.latency_ms,
                        outcome="success",
                        prompt_tokens=response.usage.prompt_tokens,
                        completion_tokens=response.usage.completion_tokens,
                        total_tokens=response.usage.total_tokens,
                    )
                )
                return response
            except RetryableProviderError as exc:
                self._record_failure(provider.name)
                telemetry.append(
                    ProviderAttemptTelemetry(
                        provider=provider.name,
                        model=exc.model or "unknown",
                        attempt=attempt,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        outcome="retryable_error",
                        error_type=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                )
                if attempt >= max_attempts:
                    return None
                continue
            except NonRetryableProviderError as exc:
                self._record_failure(provider.name)
                telemetry.append(
                    ProviderAttemptTelemetry(
                        provider=provider.name,
                        model=exc.model or "unknown",
                        attempt=attempt,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        outcome="non_retryable_error",
                        error_type=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                )
                return None
            except ProviderError as exc:
                self._record_failure(provider.name)
                telemetry.append(
                    ProviderAttemptTelemetry(
                        provider=provider.name,
                        model=exc.model or "unknown",
                        attempt=attempt,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        outcome="provider_error",
                        error_type=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                )
                return None
        return None

    def _build_prompt(
        self,
        *,
        query: str,
        snippets: list[RetrievedSnippet],
        memories: list[dict[str, object]],
    ) -> list[dict[str, str]]:
        context_chunks = "\n\n".join(
            f"[{s.file_path}:{s.start_line}-{s.end_line}]\n{s.chunk_text}" for s in snippets
        )
        memory_text = "\n".join(
            f"- {m.get('subject', 'memory')}: {m.get('fact', '')}" for m in memories
        )
        system = (
            "You are a repository-aware assistant. Ground answers in retrieved snippets and memory. "
            "If evidence is missing, say so clearly."
        )
        user = (
            f"Question: {query}\n\n"
            f"Retrieved snippets:\n{context_chunks or 'None'}\n\n"
            f"Memories:\n{memory_text or 'None'}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _is_circuit_open(self, provider_name: str) -> bool:
        state = self._circuit_breakers.setdefault(provider_name, _CircuitState())
        if state.opened_until is None:
            return False
        if datetime.now(timezone.utc) >= state.opened_until:
            state.opened_until = None
            state.failures = 0
            return False
        return True

    def _record_failure(self, provider_name: str) -> None:
        state = self._circuit_breakers.setdefault(provider_name, _CircuitState())
        state.failures += 1
        if state.failures >= self.execution_config.circuit_breaker_failure_threshold:
            state.opened_until = datetime.now(timezone.utc) + timedelta(
                seconds=self.execution_config.circuit_breaker_reset_seconds
            )

    def _record_success(self, provider_name: str) -> None:
        state = self._circuit_breakers.setdefault(provider_name, _CircuitState())
        state.failures = 0
        state.opened_until = None
