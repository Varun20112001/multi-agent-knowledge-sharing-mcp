import secrets
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.engine import SessionLocal
from app.db.models import ApiKey, Project
from app.security.api_keys import hash_api_key, hash_api_key_secret


ALPHABET = string.ascii_letters + string.digits


def _random_token(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def issue_key(project_name: str, label: str, rotate: bool) -> str:
    key_id = _random_token(16)
    secret = _random_token(40)
    full_key = f"{key_id}.{secret}"

    with SessionLocal() as db:
        project = db.execute(select(Project).where(Project.name == project_name)).scalar_one_or_none()
        if project is None:
            raise ValueError(f"Project not found: {project_name}")

        if rotate:
            active_keys = db.execute(
                select(ApiKey).where(ApiKey.project_id == project.id, ApiKey.is_active.is_(True))
            ).scalars().all()
            for existing in active_keys:
                existing.is_active = False
                existing.revoked_at = datetime.now(timezone.utc)

        db.add(
            ApiKey(
                project_id=project.id,
                key_id=key_id,
                key_secret_hash=hash_api_key_secret(secret),
                key_hash=hash_api_key(full_key),
                label=label,
                is_active=True,
            )
        )
        db.commit()

    return full_key


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("Usage: python scripts/issue_api_key.py <project_name> <label> [--rotate]")

    project_name = sys.argv[1]
    label = sys.argv[2]
    rotate = "--rotate" in sys.argv[3:]

    created = issue_key(project_name, label, rotate)
    print("api_key=" + created)
