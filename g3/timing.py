"""
The frozen G3-C timing (NOTES 70.6.1 / 70.6.3) — single definition of
the acquisition schedule and the daily arm's four timestamps.

FETCH_UTC_HOUR is a genuine UTC constant because it is OUR OWN
supervisor schedule (cron runs in UTC, DST-invariant by design); the
68.11.1.1 ban on UTC release constants governs PUBLISHER schedules,
which stay zoneinfo-local in tools/g3_exogenous_loader.py.

    22:00 UTC day D    decision_time = feature_cutoff_time
    00:00 UTC D+1      target_start_time (hypothetical execution)
    00:00 UTC D+2      target_end_time — the next COMPLETE UTC day

The strategy does not notionally enter at 22:00: the 22:00 observation
is a decision snapshot; execution is defined at the next 00:00 UTC
boundary; the target is the next complete UTC-day return. No
information arriving between 22:00 and 00:00 may enter that forecast.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

FETCH_UTC_HOUR = 22          # the supervisor's own daily schedule (70.6.1)
CUTOFF_UTC_HOUR = 22         # decision_time == feature_cutoff_time (70.6.3)


def t_usable(publisher_release: datetime) -> datetime:
    """The first scheduled fetch instant >= the publisher's release —
    the frozen possession rule (70.6.1): the bot knows a value when its
    job runs, not when the publisher posts."""
    if publisher_release.tzinfo is None:
        raise ValueError("publisher_release must be timezone-aware")
    r = publisher_release.astimezone(timezone.utc)
    fetch = r.replace(hour=FETCH_UTC_HOUR, minute=0, second=0,
                      microsecond=0)
    if fetch < r:
        fetch += timedelta(days=1)
    return fetch


def decision_time(d: date) -> datetime:
    """22:00:00 UTC on day D — the feature cutoff for forecasting D+1."""
    return datetime.combine(d, time(CUTOFF_UTC_HOUR), tzinfo=timezone.utc)


def target_window(d: date) -> tuple[datetime, datetime]:
    """[00:00 D+1, 00:00 D+2) — the next complete UTC day (70.6.3)."""
    start = datetime.combine(d + timedelta(days=1), time(0),
                             tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def last_complete_day(d: date) -> date:
    """At the day-D cutoff the most recent COMPLETE UTC day is D-1."""
    return d - timedelta(days=1)
