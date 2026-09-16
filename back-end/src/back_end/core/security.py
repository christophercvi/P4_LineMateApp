import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from back_end.core.config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(claims: dict[str, Any], minutes: int | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        **claims,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes or settings.access_token_minutes)).timestamp()),
        "iss": "linemate",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer="linemate",
        options={"require": ["exp", "iat", "sub"]},
    )


def _file_signature(asset_id: str, expires: int) -> str:
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{asset_id}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]


def signed_file_url(asset_id: str, ttl: int | None = None) -> str:
    expires = int(time.time()) + (ttl or get_settings().file_url_ttl_seconds)
    return f"/api/files/{asset_id}?exp={expires}&sig={_file_signature(asset_id, expires)}"


def verify_file_signature(asset_id: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(_file_signature(asset_id, expires), signature)


SERVICE_TOKEN_PREFIX = "lm_svc_"


def new_service_token() -> tuple[str, str]:
    """Returns (plain token, short lookup prefix). Only the argon2 hash of the token is stored."""
    raw = secrets.token_urlsafe(32)
    token = f"{SERVICE_TOKEN_PREFIX}{raw}"
    return token, token[: len(SERVICE_TOKEN_PREFIX) + 8]


def token_lookup_prefix(token: str) -> str:
    return token[: len(SERVICE_TOKEN_PREFIX) + 8]
