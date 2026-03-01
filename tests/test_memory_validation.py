from uuid import uuid4

import pytest

from app.memory.service import MemoryValidationError, _normalized_path, validate_memory_request
from app.schemas.api import Citation, StoreMemoryRequest


class FakeDB:
    pass


def test_normalized_path_handles_windows_and_slashes() -> None:
    assert _normalized_path(r"orders\\apis\\x.py") == "orders/apis/x.py"
    assert _normalized_path("/orders/apis/x.py/") == "orders/apis/x.py"


def test_memory_validation_rejects_missing_citations() -> None:
    payload = StoreMemoryRequest(project_id=uuid4(), subject="s", fact="f", citations=[])
    with pytest.raises(MemoryValidationError) as exc:
        validate_memory_request(FakeDB(), payload)
    assert exc.value.code == "INVALID_SCHEMA"


def test_memory_validation_rejects_when_project_not_ingested(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import memory

    monkeypatch.setattr(memory.service, "_project_has_chunks", lambda *args, **kwargs: False)
    payload = StoreMemoryRequest(
        project_id=uuid4(),
        subject="s",
        fact="f",
        citations=[{"file_path": "a.py", "line_start": 1}],
    )
    with pytest.raises(MemoryValidationError) as exc:
        validate_memory_request(FakeDB(), payload)
    assert exc.value.code == "PROJECT_NOT_INGESTED"


def test_memory_validation_accepts_url_compat_field(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import memory

    monkeypatch.setattr(memory.service, "_project_has_chunks", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        memory.service,
        "_resolve_citation",
        lambda *args, **kwargs: Citation(file_path="orders/apis/x.py", line_start=1),
    )

    payload = StoreMemoryRequest(
        project_id=uuid4(),
        subject="s",
        fact="f",
        citations=[{"url": "orders/apis/x.py", "line_start": 1}],
    )

    normalized = validate_memory_request(FakeDB(), payload)
    assert normalized[0].file_path == "orders/apis/x.py"
