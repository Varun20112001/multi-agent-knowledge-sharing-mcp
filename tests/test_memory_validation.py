from uuid import uuid4

import pytest

from app.memory.service import MemoryValidationError, validate_memory_request
from app.schemas.api import StoreMemoryRequest


class FakeDB:
    pass


def test_memory_validation_rejects_missing_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = StoreMemoryRequest(project_id=uuid4(), subject="s", fact="f", citations=[])
    with pytest.raises(MemoryValidationError):
        validate_memory_request(FakeDB(), payload)


def test_memory_validation_rejects_unresolved_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import memory

    monkeypatch.setattr(memory.service, "_citation_resolves", lambda *args, **kwargs: False)
    payload = StoreMemoryRequest(
        project_id=uuid4(),
        subject="s",
        fact="f",
        citations=[{"file_path": "a.py", "line_start": 1}],
    )
    with pytest.raises(MemoryValidationError):
        validate_memory_request(FakeDB(), payload)
