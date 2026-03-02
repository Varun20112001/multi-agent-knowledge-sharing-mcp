from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.config import get_settings


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str, model: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class RetryableProviderError(ProviderError):
    """Error class for transient provider failures suitable for retry/fallback."""


class NonRetryableProviderError(ProviderError):
    """Error class for permanent provider failures (bad request/auth/etc)."""


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class NormalizedLLMResponse:
    provider: str
    model: str
    content: str
    raw: dict[str, Any]
    usage: LLMUsage
    latency_ms: float
    finish_reason: str | None = None


class LLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
        timeout_s: float | None = None,
    ) -> NormalizedLLMResponse:
        raise NotImplementedError


def _as_usage(usage: Any) -> LLMUsage:
    if usage is None:
        return LLMUsage()
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", prompt + completion) or 0)
    return LLMUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


def _classify_provider_error(exc: Exception, *, provider: str, model: str | None) -> ProviderError:
    name = exc.__class__.__name__.lower()
    message = str(exc)
    retryable_markers = (
        "timeout",
        "connection",
        "tempor",
        "rate",
        "overload",
        "unavailable",
        "apierror",
    )
    if any(marker in name or marker in message.lower() for marker in retryable_markers):
        return RetryableProviderError(message, provider=provider, model=model)
    return NonRetryableProviderError(message, provider=provider, model=model)


class OpenAILLMProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM provider includes openai")
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.openai_api_key)

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
        timeout_s: float | None = None,
    ) -> NormalizedLLMResponse:
        selected_model = model or "gpt-4.1-mini"
        started = time.perf_counter()
        try:
            resp = self.client.chat.completions.create(
                model=selected_model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                timeout=timeout_s,
            )
        except Exception as exc:
            raise _classify_provider_error(exc, provider=self.name, model=selected_model) from exc

        content = resp.choices[0].message.content or ""
        finish_reason = resp.choices[0].finish_reason
        return NormalizedLLMResponse(
            provider=self.name,
            model=resp.model,
            content=content,
            raw=resp.model_dump(),
            usage=_as_usage(resp.usage),
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason,
        )


class AnthropicLLMProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM provider includes anthropic")
        from anthropic import Anthropic

        self.client = Anthropic(api_key=settings.anthropic_api_key)

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
        timeout_s: float | None = None,
    ) -> NormalizedLLMResponse:
        selected_model = model or "claude-3-5-sonnet-latest"
        started = time.perf_counter()
        try:
            resp = self.client.messages.create(
                model=selected_model,
                max_tokens=1024,
                messages=messages,
                temperature=temperature,
                timeout=timeout_s,
            )
        except Exception as exc:
            raise _classify_provider_error(exc, provider=self.name, model=selected_model) from exc

        content = "\n".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text" and getattr(block, "text", None)
        )
        usage = LLMUsage(
            prompt_tokens=int(getattr(resp.usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(resp.usage, "output_tokens", 0) or 0),
            total_tokens=int((getattr(resp.usage, "input_tokens", 0) or 0) + (getattr(resp.usage, "output_tokens", 0) or 0)),
        )
        return NormalizedLLMResponse(
            provider=self.name,
            model=resp.model,
            content=content,
            raw=resp.model_dump(),
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=getattr(resp, "stop_reason", None),
        )


class LocalLLMProvider(LLMProvider):
    name = "local"

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
        timeout_s: float | None = None,
    ) -> NormalizedLLMResponse:
        raise NonRetryableProviderError("Local provider is not configured in V1", provider=self.name, model=model)


def get_llm_provider(provider_name: str | None = None) -> LLMProvider:
    provider = (provider_name or get_settings().llm_provider).lower()
    if provider == "openai":
        return OpenAILLMProvider()
    if provider == "anthropic":
        return AnthropicLLMProvider()
    if provider == "local":
        return LocalLLMProvider()
    raise ValueError(f"Unsupported LLM provider: {provider}")
