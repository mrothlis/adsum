"""Attendance scoring policy, in one place.

Every surface that shows or exports a percentage must call score() so the
gradebook, the roster table, the at-risk flags and the analytics page can
never disagree about what a student's attendance is worth.

Policy:
  * Excused absences are removed from the denominator entirely -- a documented
    absence neither helps nor hurts.
  * Each student gets a number of free unexcused absences (default 3). Those
    are forgiven before the percentage is worked out.
  * The result is capped at 100, and a course with nothing to count scores 100
    rather than 0, so a student is never punished for an empty gradebook.
"""

from __future__ import annotations

from dataclasses import dataclass

PRESENT_STATUSES = ("present", "late")
EXCUSED_STATUS = "excused"


@dataclass
class Score:
    attended: int          # present or late
    excused: int           # excluded from the denominator
    unexcused: int         # absences that were not excused
    forgiven: int          # how many of those the allowance absorbed
    counted_total: int     # denominator after excused removed
    pct: int               # 0-100, policy applied

    @property
    def unexcused_over_limit(self) -> int:
        """Unexcused absences beyond the allowance -- what actually costs them."""
        return max(0, self.unexcused - self.forgiven)


def score(total_sessions: int, attended: int, excused: int = 0,
          allowance: int = 3) -> Score:
    """Apply the attendance policy. All arguments are counts of sessions."""
    total_sessions = max(0, int(total_sessions))
    attended = max(0, int(attended))
    excused = max(0, int(excused))
    allowance = max(0, int(allowance))

    excused = min(excused, total_sessions)
    counted_total = max(0, total_sessions - excused)
    attended = min(attended, counted_total)

    unexcused = max(0, counted_total - attended)
    forgiven = min(unexcused, allowance)

    if counted_total <= 0:
        pct = 100
    else:
        pct = round((attended + forgiven) / counted_total * 100)
        pct = max(0, min(100, pct))

    return Score(attended=attended, excused=excused, unexcused=unexcused,
                 forgiven=forgiven, counted_total=counted_total, pct=pct)
