import secrets
import string
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

# Ensure repo root is importable when script is executed as a file path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.engine import SessionLocal
from app.db.models import ApiKey, Project
from app.security.api_keys import hash_api_key, hash_api_key_secret

PROJECT_NAME = "local-project"
ALPHABET = string.ascii_letters + string.digits


def _random_token(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


with SessionLocal() as db:
    project = db.execute(select(Project).where(Project.name == PROJECT_NAME)).scalar_one_or_none()
    if project is None:
        project = Project(id=uuid.uuid4(), name=PROJECT_NAME)
        db.add(project)
        db.flush()

    existing = db.execute(select(ApiKey).where(ApiKey.project_id == project.id)).scalar_one_or_none()
    raw_api_key = ""
    if existing is None:
        key_id = _random_token(16)
        secret = _random_token(40)
        raw_api_key = f"{key_id}.{secret}"
        db.add(
            ApiKey(
                project_id=project.id,
                key_id=key_id,
                key_secret_hash=hash_api_key_secret(secret),
                key_hash=hash_api_key(raw_api_key),
                label="local-dev",
                is_active=True,
            )
        )

    db.commit()
    print(f"project_id={project.id}")
    if raw_api_key:
        print(f"api_key={raw_api_key}")
    else:
        print("api_key=<existing key unchanged>")
