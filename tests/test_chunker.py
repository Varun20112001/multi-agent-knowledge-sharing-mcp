from app.ingestion.chunker import chunk_text_by_lines


def test_chunk_boundaries_preserve_lines() -> None:
    content = "\n".join(f"line {i}" for i in range(1, 151))
    chunks = chunk_text_by_lines(content, max_lines=50, overlap=10)

    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 50
    assert chunks[1].start_line == 41
    assert chunks[1].end_line == 90
    assert chunks[-1].end_line == 150
