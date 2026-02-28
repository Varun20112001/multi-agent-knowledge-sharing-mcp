from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    chunk_index: int
    start_line: int
    end_line: int


def chunk_text_by_lines(content: str, max_lines: int = 80, overlap: int = 10) -> list[Chunk]:
    if max_lines <= overlap:
        raise ValueError("max_lines must be greater than overlap")

    lines = content.splitlines()
    if not lines:
        return []

    chunks: list[Chunk] = []
    step = max_lines - overlap
    chunk_index = 0
    for start in range(0, len(lines), step):
        end = min(start + max_lines, len(lines))
        text = "\n".join(lines[start:end]).strip()
        if text:
            chunks.append(
                Chunk(
                    text=text,
                    chunk_index=chunk_index,
                    start_line=start + 1,
                    end_line=end,
                )
            )
            chunk_index += 1
        if end == len(lines):
            break
    return chunks
