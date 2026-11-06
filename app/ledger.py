"""The attendance ledger: a round-trippable record carried in one D2L column.

This module is why the app does not need to be the system of record. The
ledger string written into D2L's "Attendance Detail" text grade item contains
every class date and its status, so the app's entire history can be rebuilt
from a D2L grade export. Wipe the database, restore from D2L, carry on.

Format (one cell per student):

    5/8 present, 3 unexcused (3 allowed), 1 excused |
    P=present L=late A=absent E=excused |
    09/01/26:P 09/03/26:A 09/05/26:E ...

The date carries a two-digit year so a restore is unambiguous. Everything
before the final "|" is human-readable summary and is ignored when parsing.
"""

from __future__ import annotations

import datetime as dt
import re

CODE_TO_STATUS = {"P": "present", "L": "late", "E": "excused", "A": None}
STATUS_TO_CODE = {"present": "P", "late": "L", "excused": "E"}

DATE_FMT = "%m/%d/%y"
_ENTRY = re.compile(r"\b(\d{2}/\d{2}/\d{2}):([PLAE])\b")


def build(entries: list[tuple[dt.datetime, str | None]], summary: str) -> str:
    """Render the ledger cell. entries is [(session_date, status_or_None), ...]."""
    parts = [f"{d.strftime(DATE_FMT)}:{STATUS_TO_CODE.get(s or '', 'A')}" for d, s in entries]
    return f"{summary} | P=present L=late A=absent E=excused | " + " ".join(parts)


def parse(cell: str) -> list[tuple[dt.date, str | None]]:
    """Recover [(date, status_or_None), ...] from a ledger cell.

    Tolerant by design: it scans for date:code tokens anywhere in the string,
    so a reworded summary or a spreadsheet mangling the prose cannot break a
    restore. Unparseable input yields an empty list rather than raising.
    """
    out: list[tuple[dt.date, str | None]] = []
    if not cell:
        return out
    for raw_date, code in _ENTRY.findall(cell):
        try:
            day = dt.datetime.strptime(raw_date, DATE_FMT).date()
        except ValueError:
            continue
        out.append((day, CODE_TO_STATUS.get(code)))
    return out


def find_detail_column(headers: list[str]) -> int | None:
    """Locate the Attendance Detail column in a D2L grade export."""
    for i, h in enumerate(headers):
        cleaned = (h or "").strip().strip('"').lower()
        if "attendance detail" in cleaned:
            return i
    return None


def find_user_column(headers: list[str]) -> int | None:
    """Locate the username column in a D2L grade export."""
    for i, h in enumerate(headers):
        cleaned = (h or "").strip().strip('"').lower()
        if cleaned in ("username", "user name") or cleaned.endswith("username"):
            return i
    return None
