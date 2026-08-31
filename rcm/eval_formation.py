"""
§60.12.2 (Stage 23b): the formation kill-criterion evaluator — criterion 1
of §60.8, completed by the user's recorded decisions.

  evaluation_start = first UTC date on which the pipeline is structurally
                     capable of a complete decision AND the pre-alpha PIT
                     risk-eligible set has >= 12 names (12 = 2 x frozen 6)
  FR windows       = EVERY completed 63-calendar-day window from
                     evaluation_start + 62 onward
  0.60 x 63 = 37.8 =>  <= 37 formed days FAILS the window; >= 38 passes
  ANY completed window failing  =>  criterion 1 FAIL

CALENDAR TIME IS NEVER COMPRESSED (§60.12.2): windows are defined by DATE
ARITHMETIC over UTC days with a fixed denominator of 63 — a date absent
from the input simply contributes no formed day, so an index-compressed
input cannot smuggle dead days out of the denominator. D_structural
(including a later fall below 12 names), D_operational, D_degenerate and
gate-failed dates all count as non-formed; only D_formed enters the
numerator.

This module reads no return and imports no data reader. It consumes a
calendar the runner produces.
"""

from __future__ import annotations

from dataclasses import dataclass

DAY_MS = 86_400_000
WINDOW_DAYS = 63                 # §60.8, frozen
FORMATION_MIN = 0.60             # §60.8, frozen
MIN_NAMES = 12                   # derived: 2 x the frozen per-leg 6
# 0.60 x 63 = 37.8: the smallest integer count that reaches the threshold.
PASS_MIN_FORMED = int(FORMATION_MIN * WINDOW_DAYS) + 1          # 38
assert PASS_MIN_FORMED == 38 and int(FORMATION_MIN * WINDOW_DAYS) == 37


@dataclass(frozen=True)
class DayRecord:
    """One UTC calendar date as the runner recorded it."""
    date_ms: int                 # UTC midnight
    category: str                # 'formed' | 'structural' | 'operational'
                                 # | 'degenerate' | 'gate'
    n_eligible: int              # pre-alpha PIT risk-eligible count
    capable: bool                # pipeline structurally capable of a
                                 # complete decision on this date


@dataclass(frozen=True)
class FormationVerdict:
    passed: bool
    evaluation_start_ms: int | None
    n_windows: int
    n_failing_windows: int
    first_failing_window_end_ms: int | None
    min_formed_in_any_window: int | None


_CATEGORIES = {"formed", "structural", "operational", "degenerate", "gate"}


def evaluation_start(records: list[DayRecord]) -> int | None:
    """§60.12.2 (USER DECISION): the first UTC date that is capable AND has
    >= 12 eligible names. None if no such date exists."""
    for r in sorted(records, key=lambda x: x.date_ms):
        if r.capable and r.n_eligible >= MIN_NAMES:
            return r.date_ms
    return None


def evaluate(records: list[DayRecord]) -> FormationVerdict:
    for r in records:
        if r.category not in _CATEGORIES:
            raise ValueError(f"unknown calendar category {r.category!r}")
        if r.date_ms % DAY_MS != 0:
            raise ValueError("date_ms must be a UTC midnight")
    start = evaluation_start(records)
    if start is None:
        return FormationVerdict(passed=False, evaluation_start_ms=None,
                                n_windows=0, n_failing_windows=0,
                                first_failing_window_end_ms=None,
                                min_formed_in_any_window=None)
    by_date = {r.date_ms: r for r in records if r.date_ms >= start}
    end = max(by_date)
    formed = {d for d, r in by_date.items() if r.category == "formed"}

    n_windows = n_fail = 0
    first_fail = None
    min_formed = None
    t = start + (WINDOW_DAYS - 1) * DAY_MS
    while t <= end:
        count = sum(1 for k in range(WINDOW_DAYS)
                    if (t - k * DAY_MS) in formed)
        n_windows += 1
        if min_formed is None or count < min_formed:
            min_formed = count
        if count < PASS_MIN_FORMED:
            n_fail += 1
            if first_fail is None:
                first_fail = t
        t += DAY_MS
    return FormationVerdict(passed=(n_windows > 0 and n_fail == 0),
                            evaluation_start_ms=start,
                            n_windows=n_windows, n_failing_windows=n_fail,
                            first_failing_window_end_ms=first_fail,
                            min_formed_in_any_window=min_formed)
