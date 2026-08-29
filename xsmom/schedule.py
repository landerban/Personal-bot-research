"""
Stage 14 B.3: when to run a cycle, and what a missed one does to the clock.

THE POLICY WAS FIXED IN NOTES 51.4 BEFORE THIS WAS WRITTEN, because deciding
it in the moment would mean deciding it after seeing which answer flatters the
28-day count.

    started within GRACE of the scheduled time  -> late_cycle,  day COUNTS
    beyond GRACE                                -> missed_cycle, day PAUSES
    unrecovered crash / unexplained mismatch    -> the §46.2 rules, day RESETS

The distinction that matters: a host that was switched off is not a failure of
the machine under test. It produces no evidence either way, so it neither
credits nor destroys the count. A crash or a shadow mismatch IS evidence about
the machine, and those still reset.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The cycle fires at 00:00 UTC plus a settle wait, so funding has settled
# before the book is decided (the Phase-1 harness uses the same grace).
CYCLE_HOUR_UTC = 0
SETTLE_GRACE_S = 15.0
# Within this of the scheduled time the cycle is simply on time -- normal
# scheduler jitter, not lateness.
ON_TIME_S = 300.0
# NOTES 51.4: the late-cycle window.
LATE_GRACE_S = 2 * 3600.0

ON_TIME, LATE, MISSED = "on_time", "late_cycle", "missed_cycle"


def scheduled_for(day: datetime) -> datetime:
    """The cycle instant for the UTC date of `day`."""
    d = day.astimezone(timezone.utc)
    return d.replace(hour=CYCLE_HOUR_UTC, minute=0, second=0,
                     microsecond=0) + timedelta(seconds=SETTLE_GRACE_S)


def next_cycle_after(now: datetime) -> datetime:
    """The next scheduled instant strictly after `now`."""
    today = scheduled_for(now)
    return today if today > now else scheduled_for(now + timedelta(days=1))


def classify(now: datetime, scheduled: datetime) -> str:
    """ON_TIME / LATE / MISSED for a cycle due at `scheduled`, run at `now`."""
    delay = (now - scheduled).total_seconds()
    if delay <= ON_TIME_S:
        return ON_TIME
    if delay <= LATE_GRACE_S:
        return LATE
    return MISSED


@dataclass
class ClockState:
    """The 28-day counter and its history. Persisted as JSON.

    `day_counter` only ever increments on a completed cycle, and only ever
    resets under the §46.2 rules. A missed day changes neither, which is the
    whole point of NOTES 51.4.
    """
    day_counter: int = 0
    day_target: int = 28
    last_cycle_date: str | None = None       # UTC date of the last COMPLETED cycle
    late_days: list[str] = field(default_factory=list)
    missed_days: list[str] = field(default_factory=list)
    resets: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str) -> "ClockState":
        p = Path(path)
        try:
            return cls(**json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    # -- transitions ------------------------------------------------------

    def record_cycle(self, date: str, kind: str) -> None:
        """A cycle COMPLETED. on_time and late_cycle both count (NOTES 51.4)."""
        if kind == MISSED:
            raise ValueError("record_cycle is for completed cycles only")
        if self.last_cycle_date == date:
            return                       # already counted today; idempotent
        self.day_counter += 1
        self.last_cycle_date = date
        if kind == LATE and date not in self.late_days:
            self.late_days.append(date)

    def record_missed(self, date: str) -> None:
        """The host was off or asleep past the grace window. The day does not
        count and the count does NOT reset -- no evidence either way."""
        if date not in self.missed_days:
            self.missed_days.append(date)

    def reset(self, date: str, reason: str) -> None:
        """§46.2 only: an unrecovered crash, or an unexplained shadow
        mismatch. Never a missed day."""
        self.resets.append({"date": date, "reason": reason,
                            "counter_was": self.day_counter})
        self.day_counter = 0
        self.last_cycle_date = None

    @property
    def complete(self) -> bool:
        return self.day_counter >= self.day_target


def due_cycle(now: datetime, state: ClockState) -> tuple[bool, str, str]:
    """(should_run, kind, utc_date) for the cycle owed at `now`.

    Answers "has today's cycle happened yet, and if not, is it still worth
    running?" -- which is the question a machine that was asleep needs asked
    on wake, not just on schedule.
    """
    sched = scheduled_for(now)
    date = sched.strftime("%Y-%m-%d")
    if now < sched:
        return False, "not_due", date
    if state.last_cycle_date == date:
        return False, "already_ran", date
    kind = classify(now, sched)
    return (kind != MISSED), kind, date
