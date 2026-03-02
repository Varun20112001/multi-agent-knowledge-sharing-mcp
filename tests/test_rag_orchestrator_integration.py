from uuid import uuid4

from app.agents.provider_router import (
    LLMProvider,
    LLMUsage,
    NonRetryableProviderError,
    NormalizedLLMResponse,
    RetryableProviderError,
)
from app.config import ProviderExecutionConfig
from app.rag.orchestrator import RAGOrchestrator
from app.retrieval.hybrid import RetrievedSnippet


class StubRetriever:
    def retrieve(self, *, project_id, query: str, top_k: int):
        return [
            RetrievedSnippet(
                id="1",
                file_path="app/main.py",
                start_line=1,
                end_line=10,
                commit_sha="abc",
                score=0.9,
                chunk_text=f"context for {query}",
            )
        ][:top_k]


class StubMemoryService:
    def recall(self, *, project_id, query: str, top_k: int):
        return [{"subject": "auth", "fact": "API keys are required"}][:top_k]


class AlwaysRetryableFailureProvider(LLMProvider):
    name = "openai"

    def generate(self, messages, tools=None, model=None, temperature=0, timeout_s=None):
        raise RetryableProviderError("temporary failure", provider=self.name, model=model or "gpt")


class NonRetryableFailureProvider(LLMProvider):
    name = "openai"

    def generate(self, messages, tools=None, model=None, temperature=0, timeout_s=None):
        raise NonRetryableProviderError("bad request", provider=self.name, model=model or "gpt")


class SuccessfulProvider(LLMProvider):
    name = "anthropic"

    def generate(self, messages, tools=None, model=None, temperature=0, timeout_s=None):
        return NormalizedLLMResponse(
            provider=self.name,
            model="claude-test",
            content="Fallback answer",
            raw={"ok": True},
            usage=LLMUsage(prompt_tokens=8, completion_tokens=6, total_tokens=14),
            latency_ms=5.5,
            finish_reason="stop",
        )


def test_orchestrator_falls_back_after_primary_retryable_failure() -> None:
    orchestrator = RAGOrchestrator(
        retriever=StubRetriever(),
        memory_service=StubMemoryService(),
        providers={"openai": AlwaysRetryableFailureProvider(), "anthropic": SuccessfulProvider()},
        execution_config=ProviderExecutionConfig(
            provider_priority=["openai", "anthropic"],
            retry_limit=1,
            circuit_breaker_failure_threshold=5,
        ),
    )

    result = orchestrator.run(project_id=uuid4(), query="How does auth work?", top_k=1)

    assert result.answer == "Fallback answer"
    assert result.provider == "anthropic"
    assert [item.outcome for item in result.telemetry] == [
        "retryable_error",
        "retryable_error",
        "success",
    ]
    assert result.telemetry[-1].total_tokens == 14


def test_orchestrator_falls_back_after_primary_non_retryable_failure() -> None:
    orchestrator = RAGOrchestrator(
        retriever=StubRetriever(),
        memory_service=StubMemoryService(),
        providers={"openai": NonRetryableFailureProvider(), "anthropic": SuccessfulProvider()},
        execution_config=ProviderExecutionConfig(
            provider_priority=["openai", "anthropic"],
            retry_limit=3,
            circuit_breaker_failure_threshold=5,
        ),
    )

    result = orchestrator.run(project_id=uuid4(), query="How does retrieval work?", top_k=1)

    assert result.provider == "anthropic"
    assert [item.outcome for item in result.telemetry] == ["non_retryable_error", "success"]
