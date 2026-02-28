import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApiKey


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_api_key(db: Session, raw_key: str) -> ApiKey:
    key_hash = hash_api_key(raw_key)
    keys = db.execute(select(ApiKey).where(ApiKey.is_active.is_(True))).scalars().all()

    for key in keys:
        if hmac.compare_digest(key.key_hash, key_hash):
            key.last_used_at = datetime.now(timezone.utc)
            return key
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key in header '{settings.auth_header_name}'",
        )
    return x_api_key
