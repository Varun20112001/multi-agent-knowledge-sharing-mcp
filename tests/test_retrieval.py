from app.retrieval.hybrid import reciprocal_rank_fusion


def test_rrf_is_monotonic() -> None:
    assert reciprocal_rank_fusion(1) > reciprocal_rank_fusion(2)
    assert reciprocal_rank_fusion(2) > reciprocal_rank_fusion(10)
