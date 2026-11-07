"""Attendance scoring policy, in one place.

Every surface that shows or exports a percentage must call score() so the
gradebook, the roster table, the at-risk flags and the analytics page can
never disagree about what a student's attendance is worth.

Policy:
  * Excused absences are removed from the denominator entirely -- a documented
    absence neither helps nor hurts.
  * A late arrival earns a share of the credit, set per course. At 100 it is
    worth a full class; at 0 it is worth nothing and behaves exactly like an
    absence.
  * Each student gets a number of free unexcused absences (default 3), applied
    to whatever credit they lost, so partial losses are absorbed too.
  * The result is capped at 100, and a course with nothing to count scores 100
    rather than 0, so a student is never punished for an empty gradebook.

Why late credit rather than a yes/no switch: in practice the QR code is only
projected at the start of class, so almost nobody can scan late -- a late
arrival is marked by the instructor by hand, and that is the moment they know
how much credit the student deserves. A boolean could only say "full credit"
or "nothing"; a percentage lets the professor say "half".
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _round_half_up(value: float) -> int:
    """Round .5 upwards, always.

    Python's built-in round() is banker's rounding: round(62.5) is 62 and
    round(63.5) is 64. For a grade that is indefensible -- a student on
    exactly 62.5% would be told 62 while another on 63.5% gets 64, and no
    syllabus describes that rule. Half-up is what everyone expects a
    percentage to do.
    """
    return int(math.floor(value + 0.5))


PRESENT_STATUSES = ("present", "late")
EXCUSED_STATUS = "excused"

# Starting point for a brand-new course, nothing more. Attendance policy is a
# syllabus decision belonging to the instructor, so it is NOT an install
# setting: whoever deploys the server has no business fixing how many
# absences a colleague's students are allowed. Each course overrides these in
# Course -> Settings -> Attendance Policy.
DEFAULT_ALLOWED_UNEXCUSED = 3
DEFAULT_LATE_THRESHOLD_MINUTES = 10
# Full credit by default: the generous choice, and it preserves the behaviour
# every existing course already had.
DEFAULT_LATE_CREDIT_PERCENT = 100


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
    late_credit_percent: int

    @property
    def late_counts_fully(self) -> bool:
        return self.late_credit_percent >= 100

    @property
    def late_counts_at_all(self) -> bool:
        return self.late_credit_percent > 0

    def describe_late(self) -> str:
        """One short phrase for the UI and the gradebook ledger."""
        if self.late_credit_percent >= 100:
            return "late counts as present"
        if self.late_credit_percent <= 0:
            return "late counts as absent"
        return f"late worth {self.late_credit_percent}%"


def policy_for(course) -> Policy:
    """This course's policy, falling back to the built-in defaults."""
    allowed = getattr(course, "allowed_unexcused_absences", None)
    late_after = getattr(course, "late_threshold_minutes", None)
    late_credit = getattr(course, "late_credit_percent", None)
    return Policy(
        allowed_unexcused=DEFAULT_ALLOWED_UNEXCUSED if allowed is None else allowed,
        late_threshold_minutes=(DEFAULT_LATE_THRESHOLD_MINUTES
                                if late_after is None else late_after),
        late_credit_percent=(DEFAULT_LATE_CREDIT_PERCENT if late_credit is None
                             else max(0, min(100, late_credit))),
    )


@dataclass
class Score:
    present: int           # full-credit attendance
    late: int              # partial-credit attendance
    excused: int           # excluded from the denominator
    absent: int            # no credit at all
    credited: float        # present + late * credit, before forgiveness
    lost: float            # credit missed, in whole-class equivalents
    forgiven: float        # how much of that the allowance absorbed
    counted_total: int     # denominator after excused removed
    pct: int               # 0-100, policy applied

    @property
    def attended(self) -> int:
        """Bodies in the room: present plus late, regardless of credit.

        Display uses this ("5/8 present"), because a student who turned up
        late was still there and telling them otherwise is simply wrong.
        """
        return self.present + self.late

    @property
    def unexcused(self) -> int:
        """Unexcused absences -- classes missed outright."""
        return self.absent

    @property
    def unexcused_over_limit(self) -> int:
        """Whole classes of lost credit beyond the allowance."""
        return max(0, _round_half_up(self.lost - self.forgiven))


def score(total_sessions: int, present: int, late: int = 0, excused: int = 0,
          allowance: int = 3, late_credit: int = 100) -> Score:
    """Apply the attendance policy. All counts are numbers of sessions.

    Reduces exactly to the previous two behaviours: late_credit=100 is the
    old "late counts as present", and late_credit=0 is the old "late counts
    as absent", including the way the allowance treats it. Anything in
    between is new.
    """
    total_sessions = max(0, int(total_sessions))
    present = max(0, int(present))
    late = max(0, int(late))
    excused = max(0, int(excused))
    allowance = max(0, int(allowance))
    late_credit = max(0, min(100, int(late_credit)))

    excused = min(excused, total_sessions)
    counted_total = max(0, total_sessions - excused)

    # Never let the attendance counts exceed what there was to attend.
    present = min(present, counted_total)
    late = min(late, counted_total - present)
    absent = max(0, counted_total - present - late)

    credited = present + late * (late_credit / 100.0)
    lost = max(0.0, counted_total - credited)
    forgiven = min(lost, float(allowance))

    if counted_total <= 0:
        pct = 100
    else:
        pct = _round_half_up((credited + forgiven) / counted_total * 100)
        pct = max(0, min(100, pct))

    return Score(present=present, late=late, excused=excused, absent=absent,
                 credited=credited, lost=lost, forgiven=forgiven,
                 counted_total=counted_total, pct=pct)
