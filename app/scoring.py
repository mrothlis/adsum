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

# Starting point for a brand-new course, nothing more. Attendance policy is a
# syllabus decision belonging to the instructor, so it is NOT an install
# setting: whoever deploys the server has no business fixing how many
# absences a colleague's students are allowed. Each course overrides these in
# Course -> Settings -> Attendance Policy.
DEFAULT_ALLOWED_UNEXCUSED = 3
DEFAULT_LATE_THRESHOLD_MINUTES = 10


@dataclass(frozen=True)
class Policy:
    """The attendance rules in force for one course.

    Every field belongs to the course. Where one is unset the built-in
    default applies -- there is deliberately no server-wide override, so an
    administrator cannot impose an attendance policy on a department.
    Resolve it once per request with policy_for() and pass it down, so one
    page can never disagree with another.
    """

    allowed_unexcused: int
    late_threshold_minutes: int
    late_counts_present: bool

    @property
    def present_statuses(self) -> tuple[str, ...]:
        """Statuses that count towards attendance."""
        return PRESENT_STATUSES if self.late_counts_present else ("present",)


def policy_for(course) -> Policy:
    """This course's policy, falling back to the built-in defaults."""
    allowed = getattr(course, "allowed_unexcused_absences", None)
    late_after = getattr(course, "late_threshold_minutes", None)
    return Policy(
        allowed_unexcused=DEFAULT_ALLOWED_UNEXCUSED if allowed is None else allowed,
        late_threshold_minutes=(DEFAULT_LATE_THRESHOLD_MINUTES
                                if late_after is None else late_after),
        late_counts_present=bool(getattr(course, "late_counts_present", True)),
    )


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
