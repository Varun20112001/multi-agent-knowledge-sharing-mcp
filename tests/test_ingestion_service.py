from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import uuid4

from app.db.models import IngestionRun
from app.ingestion.loader import RepoFile
from app.ingestion.service import IngestionDiff, execute_ingestion_run
from app.schemas.api import IngestRepoRequest


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class FakeResult:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class FakeDB:
    def __init__(self, run: IngestionRun) -> None:
        self.run = run

    def get(self, model: object, run_id: object) -> IngestionRun | None:
        return self.run if self.run.id == run_id else None

    def execute(self, *args: object, **kwargs: object) -> FakeResult:
        return FakeResult()


def _payload(project_id):
    return IngestRepoRequest(project_id=project_id, repo_path=".")


def test_unchanged_commit_is_noop(monkeypatch) -> None:
    project_id = uuid4()
    run = IngestionRun(id=uuid4(), project_id=project_id, repo_path=".", head_commit_sha="abc", status="running")
    db = FakeDB(run)

    monkeypatch.setattr("app.ingestion.service.acquire_repo_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.ingestion.service._compute_diff",
        lambda *args, **kwargs: IngestionDiff(True, [], [], []),
    )

    called = {"write": 0}
    monkeypatch.setattr(
        "app.ingestion.service.write_last_successful_commit",
        lambda *args, **kwargs: called.__setitem__("write", called["write"] + 1),
    )

    out = execute_ingestion_run(db, run.id, _payload(project_id), FakeEmbedder())
    assert out.status == "success"
    assert out.files_changed == 0
    assert out.chunks_upserted == 0
    assert called["write"] == 0


def test_modified_file_partial_update(monkeypatch) -> None:
    project_id = uuid4()
    run = IngestionRun(id=uuid4(), project_id=project_id, repo_path=".", head_commit_sha="def", status="running")
    db = FakeDB(run)

    monkeypatch.setattr("app.ingestion.service.acquire_repo_lock", lambda *args, **kwargs: None)
    changed_file = RepoFile(repo_path=".", file_path="a.py", absolute_path="/tmp/a.py", language="python", content="x=1")
    monkeypatch.setattr(
        "app.ingestion.service._compute_diff",
        lambda *args, **kwargs: IngestionDiff(False, [changed_file], ["a.py"], []),
    )
    monkeypatch.setattr("app.ingestion.service._process_changed_files", lambda *args, **kwargs: 3)

    deactivate_calls: list[list[str]] = []

    def _deactivate(*args, **kwargs):
        file_paths = args[3]
        deactivate_calls.append(file_paths)
        return 2 if file_paths else 0

    monkeypatch.setattr("app.ingestion.service._deactivate_file_paths", _deactivate)

    writes = {"count": 0}
    monkeypatch.setattr(
        "app.ingestion.service.write_last_successful_commit",
        lambda *args, **kwargs: writes.__setitem__("count", writes["count"] + 1),
    )

    out = execute_ingestion_run(db, run.id, _payload(project_id), FakeEmbedder())
    assert out.status == "success"
    assert out.files_scanned == 1
    assert out.files_changed == 1
    assert out.chunks_upserted == 3
    assert out.chunks_deactivated == 2
    assert deactivate_calls[0] == ["a.py"]
    assert writes["count"] == 1


def test_deleted_file_deactivation(monkeypatch) -> None:
    project_id = uuid4()
    run = IngestionRun(id=uuid4(), project_id=project_id, repo_path=".", head_commit_sha="ghi", status="running")
    db = FakeDB(run)

    monkeypatch.setattr("app.ingestion.service.acquire_repo_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.ingestion.service._compute_diff",
        lambda *args, **kwargs: IngestionDiff(False, [], [], ["dead.py"]),
    )
    monkeypatch.setattr("app.ingestion.service._process_changed_files", lambda *args, **kwargs: 0)

    calls = {"n": 0}

    def _deactivate(*args, **kwargs):
        calls["n"] += 1
        return 5 if calls["n"] == 2 else 0

    monkeypatch.setattr("app.ingestion.service._deactivate_file_paths", _deactivate)
    monkeypatch.setattr("app.ingestion.service.write_last_successful_commit", lambda *args, **kwargs: None)

    out = execute_ingestion_run(db, run.id, _payload(project_id), FakeEmbedder())
    assert out.status == "success"
    assert out.files_changed == 1
    assert out.chunks_deactivated == 5


def test_concurrent_runs_on_same_repo_are_guarded(monkeypatch) -> None:
    project_id = uuid4()
    payload = _payload(project_id)

    run1 = IngestionRun(id=uuid4(), project_id=project_id, repo_path=".", head_commit_sha="c1", status="running")
    run2 = IngestionRun(id=uuid4(), project_id=project_id, repo_path=".", head_commit_sha="c2", status="running")
    db1 = FakeDB(run1)
    db2 = FakeDB(run2)

    state_lock = threading.Lock()
    metrics_lock = threading.Lock()
    active = 0
    max_active = 0

    def _lock_guard(*args, **kwargs):
        nonlocal active, max_active
        with state_lock:
            with metrics_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with metrics_lock:
                active -= 1

    monkeypatch.setattr("app.ingestion.service.acquire_repo_lock", _lock_guard)
    monkeypatch.setattr(
        "app.ingestion.service._compute_diff",
        lambda *args, **kwargs: IngestionDiff(True, [], [], []),
    )

    t1 = threading.Thread(target=execute_ingestion_run, args=(db1, run1.id, payload, FakeEmbedder()))
    t2 = threading.Thread(target=execute_ingestion_run, args=(db2, run2.id, payload, FakeEmbedder()))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert max_active == 1
    assert run1.status == "success"
    assert run2.status == "success"
