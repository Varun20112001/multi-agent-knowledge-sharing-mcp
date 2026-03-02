from __future__ import annotations

from uuid import uuid4

from app.retrieval.hybrid import hybrid_search


class FakeMappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return FakeMappings(self._rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt, params):
        cursor_score = params["cursor_score"]
        cursor_id = params["cursor_id"]
        filtered = list(self.rows)
        if cursor_score is not None:
            filtered = [
                row
                for row in filtered
                if row["score"] < cursor_score or (row["score"] == cursor_score and row["id"] > cursor_id)
            ]
        filtered.sort(key=lambda row: (-row["score"], row["id"]))
        return FakeResult(filtered[: params["limit_plus_one"]])


def _rows():
    return [
        {
            "id": "a",
            "file_path": "app/a.py",
            "start_line": 1,
            "end_line": 4,
            "commit_sha": "c1",
            "chunk_text": "alpha",
            "score": 0.9,
        },
        {
            "id": "b",
            "file_path": "app/b.py",
            "start_line": 1,
            "end_line": 4,
            "commit_sha": "c1",
            "chunk_text": "beta",
            "score": 0.9,
        },
        {
            "id": "c",
            "file_path": "app/c.py",
            "start_line": 5,
            "end_line": 8,
            "commit_sha": "c1",
            "chunk_text": "gamma",
            "score": 0.8,
        },
    ]


def test_hybrid_search_ordering_is_stable_for_ties() -> None:
    db = FakeDB(_rows())

    page = hybrid_search(
        db=db,
        project_id=uuid4(),
        query="test",
        query_embedding=[0.1, 0.2],
        top_k=2,
    )

    assert [item.id for item in page.items] == ["a", "b"]
    assert page.next_cursor is not None


def test_hybrid_search_cursor_pagination_advances_without_repeats() -> None:
    db = FakeDB(_rows())

    page1 = hybrid_search(
        db=db,
        project_id=uuid4(),
        query="test",
        query_embedding=[0.1, 0.2],
        top_k=2,
    )
    page2 = hybrid_search(
        db=db,
        project_id=uuid4(),
        query="test",
        query_embedding=[0.1, 0.2],
        top_k=2,
        cursor=page1.next_cursor,
    )

    assert [item.id for item in page1.items] == ["a", "b"]
    assert [item.id for item in page2.items] == ["c"]
    assert page2.next_cursor is None
