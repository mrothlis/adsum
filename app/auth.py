"""Authentication helpers — password hashing, session cookies."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from fastapi import Request, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)

COOKIE_NAME = "checkin_session"
MAX_AGE = 60 * 60 * 12  # 12 hours


def hash_password(plain: str) -> str:
    """Hash a password using PBKDF2-SHA256 (stdlib, no passlib/bcrypt)."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iterations=260_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a PBKDF2-SHA256 hash."""
    try:
        salt_hex, dk_hex = hashed.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iterations=260_000)
        return hmac.compare_digest(dk, expected)
    except (ValueError, IndexError):
        return False


def create_session_token(data: dict) -> str:
    """Create a signed session token containing *data*."""
    return _serializer.dumps(data)


def decode_session_token(token: str, max_age: int = MAX_AGE) -> Optional[dict]:
    """Return the payload dict or None if invalid/expired."""
    try:
        return _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def set_session_cookie(response: Response, data: dict) -> None:
    token = create_session_token(data)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=MAX_AGE, httponly=True, samesite="none", secure=True,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def get_current_user(request: Request) -> Optional[dict]:
    """Read session cookie and return user dict or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return decode_session_token(token)
