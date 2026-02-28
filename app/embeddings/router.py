from app.config import get_settings
from app.embeddings.openai_provider import OpenAIEmbeddingProvider
from app.embeddings.provider import EmbeddingProvider


class LocalFallbackEmbeddingProvider(EmbeddingProvider):
    """Deterministic fallback embeddings for local/dev when provider is unavailable."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = sum(ord(ch) for ch in text)
            vectors.append([((seed + i) % 997) / 997 for i in range(self.dim)])
        return vectors


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider = settings.embed_provider.lower()
    if provider == "openai":
        if settings.openai_api_key:
            return OpenAIEmbeddingProvider()
        return LocalFallbackEmbeddingProvider(dim=settings.embedding_dim)
    if provider == "local":
        return LocalFallbackEmbeddingProvider(dim=settings.embedding_dim)
    raise ValueError(f"Unsupported embedding provider: {provider}")
