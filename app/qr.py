"""QR code generation and HMAC token logic."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import time
from typing import Tuple

import qrcode  # type: ignore

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


# A typed fallback for the student whose camera will not focus.
#
# The projected URL carries a 64-character HMAC, so the whole thing is 133
# characters -- unusable to type on a phone, which made "Camera not working?
# Open this address:" a dead end. This is the same proof in a form a person
# can read off a screen and type.
#
# It needs no table and no new state: the code is derived from the same HMAC
# over the same (session, slot) pair, so it rotates with the QR, expires with
# it, and carries exactly the same security property -- you must be able to
# see the screen right now. A code that lived longer than the QR would be a
# code a student could text to an absent friend.
#
# The alphabet drops 0, O, 1 and I, because this gets read off a projector at
# the back of a lecture theatre and typed by someone in a hurry. L is kept: it
# is only confusable with 1 and I, and both are gone. Measured uniform across
# all 32 symbols at every position, so the space is a true 32^6.
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 6


def generate_code(session_id: int, slot: int) -> str:
    """The short typeable equivalent of this session's current QR token."""
    digest = hmac.new(_SECRET, f"code:{session_id}:{slot}".encode(),
                      hashlib.sha256).digest()
    n = int.from_bytes(digest[:8], "big")
    out = []
    for _ in range(CODE_LENGTH):
        n, rem = divmod(n, len(_CODE_ALPHABET))
        out.append(_CODE_ALPHABET[rem])
    return "".join(out)


def current_slot() -> int:
    return int(time.time()) // settings.QR_ROTATION_SECONDS


def resolve_code(code: str, candidate_session_ids: list[int]
                 ) -> Tuple[bool, int | None, str]:
    """Find which session a typed code belongs to.

    The code deliberately does not encode the session id -- that would spend
    characters the student has to type, and would leak how many sessions the
    installation has. Instead the server recomputes the expected code for
    every session that is currently open, across the same two-slot grace
    window the token path allows, and looks for a match. The candidate set is
    tiny (one open session per course, enforced by a unique index).

    Returns (valid, session_id, reason), matching verify_token's shape so the
    caller can treat both paths identically.
    """
    typed = "".join(ch for ch in code.upper()
                    if ch.isalnum())
    if len(typed) != CODE_LENGTH:
        return False, None, f"A code is {CODE_LENGTH} characters."

    slot = current_slot()
    matches = set()
    for session_id in candidate_session_ids:
        for candidate_slot in (slot, slot - 1):
            expected = generate_code(session_id, candidate_slot)
            if hmac.compare_digest(typed, expected):
                matches.add(session_id)

    if not matches:
        return False, None, ("That code is not valid any more. Codes change "
                             "every few seconds -- use the one on the screen "
                             "right now.")
    if len(matches) > 1:
        # Astronomically unlikely, and guessing would be worse than refusing.
        return False, None, "Please scan the QR code instead."
    return True, matches.pop(), "OK"


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
