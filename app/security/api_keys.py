import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError
from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApiKey

logger = logging.getLogger(__name__)
password_hasher = PasswordHasher()


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def hash_api_key_secret(secret: str) -> str:
    return password_hasher.hash(secret)


def _parse_key(raw_key: str) -> tuple[str, str] | None:
    key_id, sep, secret = raw_key.partition(".")
    if not sep or not key_id or not secret:
        return None
    return key_id, secret


def _audit_log(result: str, key_id: str | None, project_id: object | None) -> None:
    logger.info(
        "api_key_auth %s",
        json.dumps(
            {
                "event": "api_key_auth",
                "result": result,
                "key_id": key_id,
                "project_id": str(project_id) if project_id else None,
            }
        ),
    )


def _verify_legacy_key(db: Session, raw_key: str) -> ApiKey:
    key_hash = hash_api_key(raw_key)
    keys = db.execute(
        select(ApiKey).where(
            ApiKey.is_active.is_(True),
            ApiKey.revoked_at.is_(None),
            ApiKey.key_id.is_(None),
        )
    ).scalars().all()

    for key in keys:
        if hmac.compare_digest(key.key_hash, key_hash):
            logger.warning(
                "api_key_auth_deprecated %s",
                json.dumps(
                    {
                        "event": "api_key_auth_deprecated",
                        "reason": "legacy_sha256_key",
                        "project_id": str(key.project_id),
                    }
                ),
            )
            key.last_used_at = datetime.now(timezone.utc)
            _audit_log("success", None, key.project_id)
            return key

    _audit_log("failure", None, None)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def verify_api_key(db: Session, raw_key: str) -> ApiKey:
    parsed = _parse_key(raw_key)
    if parsed is None:
        return _verify_legacy_key(db, raw_key)

    key_id, secret = parsed
    key = db.execute(select(ApiKey).where(ApiKey.key_id == key_id)).scalar_one_or_none()
    if key is None:
        _audit_log("failure", key_id, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    if not key.is_active or key.revoked_at is not None or not key.key_secret_hash:
        _audit_log("failure", key_id, key.project_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    try:
        valid = password_hasher.verify(key.key_secret_hash, secret)
    except (VerificationError, InvalidHash):
        valid = False

    if not valid:
        _audit_log("failure", key_id, key.project_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    key.last_used_at = datetime.now(timezone.utc)
    _audit_log("success", key_id, key.project_id)
    return key


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key in header '{settings.auth_header_name}'",
        )
    return x_api_key
