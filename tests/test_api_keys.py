from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import ApiKey
from app.security.api_keys import hash_api_key, hash_api_key_secret, verify_api_key


class _ScalarResult:
    def __init__(self, one: ApiKey | None = None, many: list[ApiKey] | None = None) -> None:
        self._one = one
        self._many = many or []

    def scalar_one_or_none(self) -> ApiKey | None:
        return self._one

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[ApiKey]:
        return self._many


class FakeDB:
    def __init__(self, by_key_id: dict[str, ApiKey] | None = None, legacy: list[ApiKey] | None = None) -> None:
        self.by_key_id = by_key_id or {}
        self.legacy = legacy or []

    def execute(self, stmt):
        where_text = str(stmt.whereclause)
        if "api_keys.key_id =" in where_text:
            key_id = stmt.whereclause.right.value
            return _ScalarResult(one=self.by_key_id.get(key_id))
        return _ScalarResult(many=self.legacy)


def _api_key(**kwargs) -> ApiKey:
    defaults = {
        "project_id": uuid4(),
        "key_hash": hash_api_key("legacy-secret"),
        "label": "test",
        "is_active": True,
        "key_id": "kid_test",
        "key_secret_hash": hash_api_key_secret("supersecret"),
        "revoked_at": None,
    }
    defaults.update(kwargs)
    return ApiKey(**defaults)


def test_verify_api_key_valid() -> None:
    key = _api_key()
    db = FakeDB(by_key_id={"kid_test": key})

    out = verify_api_key(db, "kid_test.supersecret")

    assert out is key
    assert out.last_used_at is not None


def test_verify_api_key_invalid_key_id() -> None:
    db = FakeDB(by_key_id={})

    with pytest.raises(HTTPException) as exc:
        verify_api_key(db, "kid_missing.supersecret")

    assert exc.value.status_code == 401


def test_verify_api_key_invalid_secret() -> None:
    key = _api_key()
    db = FakeDB(by_key_id={"kid_test": key})

    with pytest.raises(HTTPException) as exc:
        verify_api_key(db, "kid_test.wrong")

    assert exc.value.status_code == 401


def test_verify_api_key_revoked() -> None:
    key = _api_key(revoked_at=datetime.now(timezone.utc))
    db = FakeDB(by_key_id={"kid_test": key})

    with pytest.raises(HTTPException) as exc:
        verify_api_key(db, "kid_test.supersecret")

    assert exc.value.status_code == 401


def test_verify_api_key_inactive() -> None:
    key = _api_key(is_active=False)
    db = FakeDB(by_key_id={"kid_test": key})

    with pytest.raises(HTTPException) as exc:
        verify_api_key(db, "kid_test.supersecret")

    assert exc.value.status_code == 401


def test_verify_api_key_legacy_sha256_compatibility(caplog: pytest.LogCaptureFixture) -> None:
    key = _api_key(key_id=None, key_secret_hash=None)
    db = FakeDB(legacy=[key])

    out = verify_api_key(db, "legacy-secret")

    assert out is key
    assert "api_key_auth_deprecated" in caplog.text
