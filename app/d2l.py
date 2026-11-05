"""D2L Brightspace helpers.

With LTI 1.1 (no IT admin needed), we don't have server-side API access
to D2L for roster pull or grade passback. Instead:

- Roster: Students self-enroll on first LTI launch (auto-created in our DB).
- Grades: Instructors export CSV from the dashboard and import into D2L gradebook.

If CSU IT later provides Valence API credentials (OAuth 2.0), these helpers
can push grades directly. For now they are stub-ready.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


async def push_grades_csv_to_d2l(
    d2l_host: str,
    org_unit_id: str,
    access_token: str,
    csv_content: str,
) -> bool:
    """Push a grades CSV to D2L via the Valence API.

    Requires an OAuth 2.0 access token with grades:gradevalues:write scope.
    This is a FUTURE capability - requires CSU IT to register an API app.
    For now, instructors manually import the CSV export into D2L.
    """
    log.info("Grade push to D2L not yet configured (requires Valence API credentials)")
    return False
