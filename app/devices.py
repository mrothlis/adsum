"""The device cookie that carries a standalone student's identity.

Kept in its own module, next to auth.py rather than inside it, because the
two cookies answer different questions and must never be confused:

    adsum_session       "which INSTRUCTOR (or LTI-launched student) is this?"
                        12 hours, issued by a password login or an LTI launch.
    adsum_device_c<id>  "which STUDENT is this phone, in THIS course?"
                        a whole term, issued only by a successful claim made
                        from inside a live check-in.

Three deliberate choices:

* **Its own itsdangerous salt.** Both cookies are signed with the same
  SECRET_KEY, so without a distinct salt a valid instructor session cookie
  would also be a structurally valid device cookie and vice versa. With the
  salt, a value lifted from one cookie fails the signature check on the
  other, and no amount of payload shaping gets around it.
* **Its own cookie name, per course.** A student can be in two standalone
  courses, each with its own binding, so one shared cookie would have to hold
  a map and a bug in merging it would bind the wrong name. The course id is in
  the NAME as well as in the signed payload, and the reader requires them to
  agree, so a cookie minted for course 4 is not even looked at on course 7.
* **Long-lived on purpose.** This is the student's identity for the term; it
  has to survive far longer than the instructor session's 12 hours or they
  would be asked who they are again every week, which is exactly the moment
  an impersonation becomes possible. Revocation does not come from the clock:
  it comes from the DeviceBinding row, which the caller must still look up.
  A released binding invalidates a perfectly well-signed cookie.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

# Distinct salt — see the module docstring. Changing this logs every student
# phone out of every standalone course, so it is a constant, not a setting.
_DEVICE_SALT = "adsum-device-binding-v1"
_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt=_DEVICE_SALT)

COOKIE_PREFIX = "adsum_device_c"

# About ten months: longer than any single term, so a binding made in week
# one still works in finals week, and it expires over a summer rather than
# living forever on a phone that has changed hands.
MAX_AGE = 60 * 60 * 24 * 300

_KIND = "device"


def cookie_name(course_id: int) -> str:
    return f"{COOKIE_PREFIX}{int(course_id)}"


def _secure() -> bool:
    # Mirrors auth.set_session_cookie: a Secure cookie is silently dropped
    # over plain http, which would make a non-TLS install impossible to use
    # with no error shown anywhere.
    return settings.BASE_URL.lower().startswith("https://")


def issue(response: Response, course_id: int, student_id: int,
          binding_id: int) -> None:
    """Put the signed binding on this phone."""
    token = _serializer.dumps({
        "kind": _KIND,
        "course_id": int(course_id),
        "student_id": int(student_id),
        "binding_id": int(binding_id),
    })
    response.set_cookie(
        cookie_name(course_id), token,
        max_age=MAX_AGE, httponly=True, samesite="lax", secure=_secure(),
    )


def clear(response: Response, course_id: int) -> None:
    """Forget the binding on this phone.

    Must mirror the attributes issue() used, or the browser keeps the
    original -- the same trap clear_session_cookie documents.
    """
    response.delete_cookie(
        cookie_name(course_id), httponly=True, samesite="lax",
        secure=_secure(),
    )


def read(request: Request, course_id: int) -> Optional[dict]:
    """The claim this phone presents for *course_id*, or None.

    Returns only a well-formed, correctly signed, in-date payload whose
    course matches the cookie it arrived in. Says nothing about whether the
    binding is still active -- that is a database question, and the caller
    must ask it, because releasing a binding has to take effect immediately.
    """
    raw = request.cookies.get(cookie_name(course_id))
    if not raw:
        return None
    try:
        payload = _serializer.loads(raw, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != _KIND:
        return None
    try:
        claim = {
            "course_id": int(payload["course_id"]),
            "student_id": int(payload["student_id"]),
            "binding_id": int(payload["binding_id"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    # The course is in the cookie name AND in the signature. A cookie minted
    # for another course is refused rather than reinterpreted.
    if claim["course_id"] != int(course_id):
        return None
    if min(claim.values()) <= 0:
        return None
    return claim
