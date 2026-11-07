"""Authentication helpers — password hashing, session cookies."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)

COOKIE_NAME = "adsum_session"
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


def decode_session_with_issued_at(token: str, max_age: int = MAX_AGE):
    """Return (payload, issued_at) so a session can be revoked by age.

    itsdangerous already embeds the signing time, so revocation needs no
    extra cookie field: comparing the issue time against an instructor's
    sessions_valid_from lets a password reset or a deactivation invalidate
    cookies that were handed out earlier.
    """
    try:
        # itsdangerous returns (payload, timestamp) in this order.
        payload, issued_at = _serializer.loads(token, max_age=max_age,
                                               return_timestamp=True)
        return payload, issued_at
    except (BadSignature, SignatureExpired):
        return None, None


def set_session_cookie(response: Response, data: dict) -> None:
    """Issue the session cookie.

    SameSite=Lax, not None. This is the primary CSRF defence: a Lax cookie is
    not sent on a cross-site POST, so an attacker's auto-submitting form can
    no longer act as a signed-in instructor. It was None because of D2L, but
    the documented setup ticks "Open as External Resource", so launches land
    in a top-level window rather than an iframe. The LTI launch itself is a
    cross-site POST that carries no cookie (it is authenticated by its OAuth
    signature) and the 302 it returns is a top-level GET, which Lax does
    send, so the flow still works.

    Secure follows BASE_URL instead of being hard-coded true: a Secure cookie
    is silently dropped on plain http, which made a non-TLS install
    impossible to log into with no error shown anywhere.
    """
    token = create_session_token(data)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=MAX_AGE, httponly=True, samesite="lax",
        secure=settings.BASE_URL.lower().startswith("https://"),
    )


def clear_session_cookie(response: Response) -> None:
    # Must mirror the attributes the cookie was set with, or the browser
    # keeps the original and "Logout" silently does nothing.
    response.delete_cookie(
        COOKIE_NAME, httponly=True, samesite="lax",
        secure=settings.BASE_URL.lower().startswith("https://"),
    )


def get_current_user(request: Request) -> Optional[dict]:
    """Read the session cookie and return the user dict, or None.

    The payload carries `_issued_at` so authorization can refuse a cookie
    that predates a password reset or a deactivation.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload, issued_at = decode_session_with_issued_at(token)
    if payload is None:
        return None
    if issued_at is not None:
        # itsdangerous returns an aware UTC datetime; the database stores
        # naive UTC, so strip the tzinfo to keep the comparison valid.
        payload = dict(payload)
        payload["_issued_at"] = issued_at.replace(tzinfo=None)
    return payload
