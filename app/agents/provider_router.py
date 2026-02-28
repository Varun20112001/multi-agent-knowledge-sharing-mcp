from abc import ABC, abstractmethod
from typing import Any

from anthropic import Anthropic
from openai import OpenAI

from app.config import get_settings


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]:
        raise NotImplementedError


class OpenAILLMProvider(LLMProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        self.client = OpenAI(api_key=settings.openai_api_key)

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]:
        selected_model = model or "gpt-4.1-mini"
        resp = self.client.chat.completions.create(
            model=selected_model,
            messages=messages,
            tools=tools,
            temperature=temperature,
        )
        return resp.model_dump()


class AnthropicLLMProvider(LLMProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        self.client = Anthropic(api_key=settings.anthropic_api_key)

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]:
        selected_model = model or "claude-3-5-sonnet-latest"
        resp = self.client.messages.create(
            model=selected_model,
            max_tokens=1024,
            messages=messages,
            temperature=temperature,
        )
        return resp.model_dump()


class LocalLLMProvider(LLMProvider):
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]:
        raise NotImplementedError("Local provider is not configured in V1")


def get_llm_provider() -> LLMProvider:
    provider = get_settings().llm_provider.lower()
    if provider == "openai":
        return OpenAILLMProvider()
    if provider == "anthropic":
        return AnthropicLLMProvider()
    if provider == "local":
        return LocalLLMProvider()
    raise ValueError(f"Unsupported LLM provider: {provider}")
