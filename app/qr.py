"""QR code generation and HMAC token logic."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import time
from typing import Tuple

import qrcode  # type: ignore
from qrcode.image.pil import PilImage  # type: ignore

from app.config import settings

_SECRET = settings.SECRET_KEY.encode()


def _make_hmac(message: str) -> str:
    return hmac.new(_SECRET, message.encode(), hashlib.sha256).hexdigest()


def generate_token(session_id: int) -> Tuple[str, int]:
    """Return (token_string, epoch_slot).

    The token encodes the session id and a time slot so it rotates
    every QR_ROTATION_SECONDS.
    """
    slot = int(time.time()) // settings.QR_ROTATION_SECONDS
    payload = f"{session_id}:{slot}"
    sig = _make_hmac(payload)
    token = f"{session_id}.{slot}.{sig}"
    return token, slot


def verify_token(token: str) -> Tuple[bool, int | None, str]:
    """Validate a check-in token.

    Returns (valid, session_id, reason).
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False, None, "Malformed token"
    try:
        session_id = int(parts[0])
        slot = int(parts[1])
        sig = parts[2]
    except ValueError:
        return False, None, "Malformed token"

    expected = _make_hmac(f"{session_id}:{slot}")
    if not hmac.compare_digest(sig, expected):
        return False, None, "Invalid signature"

    current_slot = int(time.time()) // settings.QR_ROTATION_SECONDS
    # Allow current slot and one previous (grace window)
    if current_slot - slot > 1:
        return False, session_id, "Token expired — please scan the latest QR code"

    return True, session_id, "OK"


def generate_qr_base64(url: str) -> str:
    """Generate a QR code PNG and return it as a base64 data-URI string."""
    qr = qrcode.QRCode(version=1, box_size=10, border=3,
                        error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"
