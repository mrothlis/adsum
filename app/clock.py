"""The current time, in the one form this application stores.

Every timestamp column in the database holds **naive UTC**: no offset, no
tzinfo, UTC by convention. That convention is load-bearing, because
`app/main.py` converts to the display timezone on the way out and would
double-shift anything that arrived already aware.

This module exists for two reasons.

The first is that `datetime.utcnow()` is deprecated and, worse than
deprecated, misleading: it returns a NAIVE datetime holding UTC, so the
obvious modernisation to `datetime.now(UTC)` returns an AWARE one and
silently changes what gets written. Comparing an aware value with a stored
naive one raises `TypeError: can't subtract offset-naive and offset-aware
datetimes`, and the places that compare are the late-arrival threshold and
the QR token window: a class in progress is the worst possible moment to
discover it. So the conversion back to naive is done here, once, rather than
thirteen times with one of them forgotten.

The second is that time was read directly from the standard library in
thirteen places, which left no seam to freeze it in a test. Anything timing
dependent had to build fixtures by subtracting days from the real clock.
"""
from __future__ import annotations

import datetime as dt


def utc_now() -> dt.datetime:
    """Now, as naive UTC, matching what every timestamp column stores.

    Prefer this to `datetime.utcnow()` (deprecated) and to
    `datetime.now(dt.UTC)` (aware, so it cannot be compared with or stored
    alongside the existing columns without conversion).
    """
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)
