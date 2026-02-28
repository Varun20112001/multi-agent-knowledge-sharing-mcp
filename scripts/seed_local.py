import uuid
import sys
from pathlib import Path

from sqlalchemy import select

# Ensure repo root is importable when script is executed as a file path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.engine import SessionLocal
from app.db.models import ApiKey, Project
from app.security.api_keys import hash_api_key


PROJECT_NAME = "local-project"
RAW_API_KEY = "dev-local-key"


with SessionLocal() as db:
    project = db.execute(select(Project).where(Project.name == PROJECT_NAME)).scalar_one_or_none()
    if project is None:
        project = Project(id=uuid.uuid4(), name=PROJECT_NAME)
        db.add(project)
        db.flush()

    existing = db.execute(select(ApiKey).where(ApiKey.project_id == project.id)).scalar_one_or_none()
    if existing is None:
        db.add(
            ApiKey(
                project_id=project.id,
                key_hash=hash_api_key(RAW_API_KEY),
                label="local-dev",
                is_active=True,
            )
        )

    db.commit()
    print(f"project_id={project.id}")
    print(f"api_key={RAW_API_KEY}")
