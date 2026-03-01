from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import RepoIngestionState


def acquire_repo_lock(db: Session, project_id: UUID, repo_path: Path) -> None:
    lock_key = f"{project_id}:{repo_path}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": lock_key})


def get_or_create_state(db: Session, project_id: UUID, repo_path: Path) -> RepoIngestionState:
    normalized_repo = str(repo_path)
    state = db.execute(
        select(RepoIngestionState).where(
            RepoIngestionState.project_id == project_id,
            RepoIngestionState.repo_path == normalized_repo,
        )
    ).scalar_one_or_none()
    if state is not None:
        return state

    state = RepoIngestionState(project_id=project_id, repo_path=normalized_repo)
    db.add(state)
    db.flush()
    return state


def read_last_successful_commit(db: Session, project_id: UUID, repo_path: Path) -> str | None:
    state = get_or_create_state(db, project_id, repo_path)
    return state.last_successful_commit_sha


def write_last_successful_commit(db: Session, project_id: UUID, repo_path: Path, commit_sha: str) -> None:
    state = get_or_create_state(db, project_id, repo_path)
    state.last_successful_commit_sha = commit_sha
