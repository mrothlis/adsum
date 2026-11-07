#!/usr/bin/env python3
"""Attendance scoring: the arithmetic that becomes somebody's grade.

Needs no server and no database. Run it after ANY change to app/scoring.py.

Two things are pinned here deliberately:

  1. Half-up rounding. Python's round() is banker's rounding, so a student on
     exactly 62.5% was told 62 while one on 63.5% got 64. No syllabus
     describes that rule, and it is the kind of defect that only surfaces as
     an argument with a student.
  2. Equivalence with the two behaviours that came before late credit became
     a percentage. late_credit=100 must reproduce the old "late counts as
     present" exactly, and late_credit=0 the old "late counts as absent",
     because existing courses were migrated onto those values and their
     grades must not move.
"""

from __future__ import annotations

import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.scoring import (  # noqa: E402
    DEFAULT_ALLOWED_UNEXCUSED,
    DEFAULT_LATE_CREDIT_PERCENT,
    Policy,
    policy_for,
    score,
)

failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f"  want {want!r}"))
    if not ok:
        failures.append(name)


class FakeCourse:
    """Stands in for a Course row; the fields are what policy_for() reads."""

    allowed_unexcused_absences: int | None = None
    late_threshold_minutes: int | None = None
    late_credit_percent: int | None = None


def _legacy(total, present, late, excused, allowance, late_counts_present) -> int:
    """The scoring that shipped before late credit was a percentage."""
    attended = present + late if late_counts_present else present
    excused = min(excused, total)
    counted = max(0, total - excused)
    attended = min(attended, counted)
    unexcused = max(0, counted - attended)
    forgiven = min(unexcused, allowance)
    if counted <= 0:
        return 100
    return max(0, min(100, int(math.floor((attended + forgiven) / counted * 100 + 0.5))))


def main() -> int:
    print("Rounding is half-up, never banker's")
    check("62.5% rounds to 63", score(8, 5, 0, 0, 0, 100).pct, 63)
    check("37.5% rounds to 38", score(8, 3, 0, 0, 0, 100).pct, 38)
    check("12.5% rounds to 13", score(8, 1, 0, 0, 0, 100).pct, 13)
    check("87.5% rounds to 88", score(8, 7, 0, 0, 0, 100).pct, 88)
    check("66.67% unaffected", score(3, 2, 0, 0, 0, 100).pct, 67)

    print("\nLate credit behaves as a share of a class")
    check("2 present 1 late 1 absent @100%", score(4, 2, 1, 0, 0, 100).pct, 75)
    check("2 present 1 late 1 absent @50%", score(4, 2, 1, 0, 0, 50).pct, 63)
    check("2 present 1 late 1 absent @0%", score(4, 2, 1, 0, 0, 0).pct, 50)
    check("partial loss absorbed by the allowance",
          score(10, 8, 2, 0, 1, 50).pct, 100)
    check("credit above 100 is clamped", score(4, 0, 4, 0, 0, 999).pct, 100)
    check("negative credit is clamped", score(4, 0, 4, 0, 0, -50).pct, 0)

    print("\nExcused absences leave the denominator")
    s = score(10, 5, 2, 1, 3, 50)
    check("excused removed from counted_total", s.counted_total, 9)
    check("attended counts bodies, not credit", s.attended, 7)
    check("absent is outright misses only", s.absent, 2)
    check("a fully excused course scores 100", score(5, 0, 0, 5, 0, 50).pct, 100)
    check("an empty course scores 100", score(0, 0, 0, 0, 3, 50).pct, 100)

    print("\nCounts can never exceed the sessions that existed")
    check("overcounting clamps the denominator", score(3, 99, 99, 0, 0, 100).counted_total, 3)
    check("overcounting clamps present", score(3, 99, 99, 0, 0, 100).present, 3)
    check("overcounting cannot exceed 100%", score(3, 99, 99, 0, 0, 100).pct, 100)
    check("negative inputs are floored", score(-5, -5, -5, -5, -5, 100).pct, 100)

    print("\nPolicy resolution falls back to the defaults, never to a server setting")
    p = policy_for(FakeCourse())
    check("inherits the default allowance", p.allowed_unexcused, DEFAULT_ALLOWED_UNEXCUSED)
    check("inherits the default late credit", p.late_credit_percent, DEFAULT_LATE_CREDIT_PERCENT)
    c = FakeCourse()
    c.allowed_unexcused_absences = 0
    c.late_credit_percent = 0
    p = policy_for(c)
    check("a zero override is honoured, not treated as unset", p.allowed_unexcused, 0)
    check("zero late credit is honoured", p.late_credit_percent, 0)
    c.late_credit_percent = 500
    check("an out-of-range stored value is clamped", policy_for(c).late_credit_percent, 100)
    check("describe_late at 100", Policy(3, 10, 100).describe_late(), "late counts as present")
    check("describe_late at 0", Policy(3, 10, 0).describe_late(), "late counts as absent")
    check("describe_late at 50", Policy(3, 10, 50).describe_late(), "late worth 50%")

    print("\nGrades of already-migrated courses do not move (8000 random cases)")
    random.seed(20261106)
    mismatch_full = mismatch_zero = 0
    for _ in range(4000):
        total = random.randint(0, 45)
        present = random.randint(0, total)
        late = random.randint(0, max(0, total - present))
        excused = random.randint(0, max(0, total - present - late))
        allowance = random.randint(0, 6)
        if score(total, present, late, excused, allowance, 100).pct != _legacy(
                total, present, late, excused, allowance, True):
            mismatch_full += 1
        if score(total, present, late, excused, allowance, 0).pct != _legacy(
                total, present, late, excused, allowance, False):
            mismatch_zero += 1
    check("late_credit=100 reproduces 'late counts as present'", mismatch_full, 0)
    check("late_credit=0 reproduces 'late counts as absent'", mismatch_zero, 0)

    print("\nPercentage is always a sane integer (20000 random cases)")
    bad = []
    for _ in range(20000):
        total = random.randint(0, 60)
        present = random.randint(0, max(0, total))
        late = random.randint(0, max(0, total - present))
        excused = random.randint(0, max(0, total - present - late))
        s = score(total, present, late, excused,
                  random.randint(0, 8), random.choice([0, 25, 50, 75, 100]))
        if not isinstance(s.pct, int) or not (0 <= s.pct <= 100):
            bad.append((total, present, late, excused, s.pct))
        if s.present + s.late + s.absent != s.counted_total:
            bad.append(("parts do not sum", total, present, late, excused))
    check("pct always an int in 0..100 and the parts always sum", bad[:3], [])

    print("-" * 48)
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
