from app.embeddings.router import LocalFallbackEmbeddingProvider


def test_local_embedding_provider_contract() -> None:
    provider = LocalFallbackEmbeddingProvider(dim=8)
    vectors = provider.embed(["abc", "def"])

    assert len(vectors) == 2
    assert all(len(vector) == 8 for vector in vectors)
    assert vectors[0] != vectors[1]
