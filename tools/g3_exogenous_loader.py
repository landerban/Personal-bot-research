"""
G3 exogenous PIT loader (NOTES §68.11.1) — the single source of release
semantics for the staged Panel-A series.

NO UTC CONSTANT FOR A RELEASE TIME EXISTS HERE OR IN THE MANIFEST
(§68.11.1.1): every release is a local wall-clock time in an IANA
timezone plus a business-calendar day offset, converted per-date with
zoneinfo — so a January and a July observation of the same source
resolve to different UTC instants, as reality does.

Four timestamps per observation (§68.11.1.2):

    observation_time        the economic period the value describes
    underlying_public_time  when the underlying first became public anywhere
    source_available_time   when THIS staged source first served it
    retrieved_at_utc        when our archived copy was obtained

ACCESS RULE, FROZEN: a model may consume a value only when
`source_available_time <= t`. `retrieved_at_utc` is auditability and
never substitutes.

Calendar (§68.11.1, recorded with the rule): ONE conservative US
business calendar — weekends, observed US federal holidays, and Good
Friday — the UNION of the Fed and NYSE/CBOE closure sets. The union can
only DELAY an assumed availability, never advance it, so its errors are
anti-lookahead by construction. Exact per-source calendars are an open
refinement (manifest `open_refinements`).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

EXO_DIR = Path(__file__).resolve().parents[1] / "data" / "exogenous"


# ------------------------------------------------------------- calendar

def _easter(year: int) -> date:
    """Gregorian computus (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year, month, weekday, n) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else \
        date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_market_holidays(year: int) -> set[date]:
    """Conservative UNION of Fed + NYSE/CBOE closures (§68.11.1)."""
    hs = {_observed(date(year, 1, 1)),
          _nth_weekday(year, 1, 0, 3),            # MLK
          _nth_weekday(year, 2, 0, 3),            # Washington
          _easter(year) - timedelta(days=2),      # Good Friday (NYSE/CBOE)
          _last_weekday(year, 5, 0),              # Memorial
          _observed(date(year, 7, 4)),
          _nth_weekday(year, 9, 0, 1),            # Labor
          _nth_weekday(year, 10, 0, 2),           # Columbus (Fed)
          _observed(date(year, 11, 11)),          # Veterans (Fed)
          _nth_weekday(year, 11, 3, 4),           # Thanksgiving
          _observed(date(year, 12, 25))}
    if year >= 2021:
        hs.add(_observed(date(year, 6, 19)))      # Juneteenth
    return hs


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in us_market_holidays(d.year)


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


# ------------------------------------------------------- release rules

@dataclass(frozen=True)
class ReleaseRule:
    tz: str                    # IANA zone — NEVER a UTC constant
    local_hour: int
    local_minute: int
    business_day_offset: int   # 0 = same day as observation, 1 = next
    calendar: str              # named per source; shared conservative impl

    def availability_utc(self, obs: date) -> datetime:
        d = add_business_days(obs, self.business_day_offset) \
            if self.business_day_offset else obs
        local = datetime.combine(d, time(self.local_hour, self.local_minute),
                                 tzinfo=ZoneInfo(self.tz))
        return local.astimezone(timezone.utc)


_NY = "America/New_York"
_H15 = ReleaseRule(_NY, 16, 15, 1, "FederalReserveBusinessCalendar")
_CBOE_CLOSE = ReleaseRule(_NY, 16, 15, 0, "CboeCalendar")
_NYSE_CLOSE = ReleaseRule(_NY, 16, 0, 0, "NyseCalendar")
# §70.8 mixed timing rule: publisher-time reconstruction is EARNED per
# series by vintage verification, never assumed. VERIFIED
# (publisher_value_equivalence in the manifest) ⇒ source == the
# publisher's documented release rule; UNVERIFIED ⇒ the conservative
# §68.12.1 rule (publisher + 1 business day, same local time) —
# handicapped, never dropped, cannot leak a revised vintage at
# publisher timing. The loader READS the manifest so the enforcement is
# structural, not conventional. fred_VIXCLS stays a cross-check under
# the conservative mirror rule regardless of its verification record.
_FRED_MIRROR = ReleaseRule(_NY, 16, 15, 1, "FederalReserveBusinessCalendar")

_PUBLISHER: dict[str, ReleaseRule] = {
    "fred_DGS2": _H15,
    "fred_DGS10": _H15,
    "fred_DTWEXBGS": _H15,
    "cboe_VIX": _CBOE_CLOSE,
    "fred_VIXCLS": _CBOE_CLOSE,
    "fred_SP500": _NYSE_CLOSE,
    "fred_NASDAQ100": _NYSE_CLOSE,
}


def _conservative(r: ReleaseRule) -> ReleaseRule:
    """§68.12.1: one business day after the publisher release, same
    local time — delay-only by construction."""
    return ReleaseRule(r.tz, r.local_hour, r.local_minute,
                       r.business_day_offset + 1, r.calendar)


def _build_rules() -> dict[str, dict[str, ReleaseRule]]:
    manifest = json.loads((EXO_DIR / "MANIFEST.json").read_text("utf-8"))
    eq = {e["key"]: e.get("publisher_value_equivalence")
          for e in manifest["series"]}
    rules = {}
    for key, pub in _PUBLISHER.items():
        if key == "fred_VIXCLS":
            src = _FRED_MIRROR
        elif eq.get(key) == "VERIFIED":
            src = pub
        else:
            src = _conservative(pub)     # UNVERIFIED never reads at
        rules[key] = {"underlying": pub, "source": src}  # publisher time
    return rules


RULES: dict[str, dict[str, ReleaseRule]] = _build_rules()


def four_timestamps(key: str, obs: date) -> dict:
    """§68.11.1.2 — the four timestamps for one observation.

    §68.12.2: `retrieved_at_utc` may be None (unknown is not estimated);
    the accompanying `retrieved_at_upper_bound_utc` and
    `retrieval_time_quality` say what is actually known about it. A
    commit-derived bound never occupies the observed-time field."""
    r = RULES[key]
    manifest = json.loads((EXO_DIR / "MANIFEST.json").read_text("utf-8"))
    entry = next(e for e in manifest["series"] if e["key"] == key)
    return {
        "observation_time": obs.isoformat(),
        "underlying_public_time":
            r["underlying"].availability_utc(obs).isoformat(),
        "source_available_time":
            r["source"].availability_utc(obs).isoformat(),
        "retrieved_at_utc": entry["retrieved_at_utc"],
        "retrieved_at_upper_bound_utc":
            entry.get("retrieved_at_upper_bound_utc"),
        "retrieval_time_quality": entry.get("retrieval_time_quality"),
    }


def load_series(key: str) -> list[tuple[date, float]]:
    """Raw rows from the staged CSV (tracked dir or the untracked raw/)."""
    for base in (EXO_DIR, EXO_DIR / "raw"):
        path = base / f"{key}.csv"
        if path.exists():
            break
    else:
        raise FileNotFoundError(f"{key}: no staged CSV (raw data for "
                                f"restricted series lives only locally)")
    out = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))
    for r in rows[1:]:
        if len(r) < 2 or r[-1].strip() in ("", "."):
            continue
        try:
            out.append((_parse_date(r[0].strip()), float(r[-1].strip())))
        except ValueError:
            continue
    return out


def _parse_date(s: str) -> date:
    """ISO first; then the MM/DD/YYYY the CBOE history file uses.

    FINDING B-1 (NOTES 69.2): the original ISO-only parse silently
    dropped every CBOE row via the ValueError-continue, so cboe_VIX
    loaded as an empty series while the manifest (whose coverage counter
    only float-checks the value column) reported it fine."""
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        m, d, y = s.split("/")
        return date(int(y), int(m), int(d))


def pit_view(key: str, t_utc: datetime) -> list[tuple[date, float]]:
    """THE ACCESS RULE: rows whose source_available_time <= t, only."""
    if t_utc.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    rule = RULES[key]["source"]
    return [(d, v) for d, v in load_series(key)
            if rule.availability_utc(d) <= t_utc]


# 70.9 three-state vintage provenance, read from the manifest so the
# enforcement is structural. PIT_RECONSTRUCTED series read ONLY from
# their revision stores; UNAVAILABLE series are unreadable by the model
# reader at any timing; VALUE_EQUIVALENT keeps the 70.8 behaviour.
STORE_PATHS: dict[str, Path] = {
    "fred_DTWEXBGS": EXO_DIR / "vintage_store_fred_DTWEXBGS.csv",
    "fred_NASDAQ100": EXO_DIR / "raw" / "vintage_store_fred_NASDAQ100.csv",
}


def _provenance() -> dict[str, str]:
    manifest = json.loads((EXO_DIR / "MANIFEST.json").read_text("utf-8"))
    return {e["key"]: e.get("vintage_provenance", "")
            for e in manifest["series"]}


class SeriesUnavailable(RuntimeError):
    """70.9.2: neither value equivalence nor a valid PIT reconstruction
    exists - the feature does not exist for Trial 1. Not a warning."""


def pit_view_reconstructed(key: str, t_utc: datetime
                           ) -> list[tuple[date, float]]:
    """70.9.3 PIT query against the revision store: for each
    observation, the value of the LATEST vintage with
    vintage_available_utc <= t. Never touches the current archive."""
    if t_utc.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    path = STORE_PATHS[key]
    latest: dict[date, tuple[str, float]] = {}
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    for r in rows[1:]:
        obs, avail, val = r[0], r[2], r[3]
        if datetime.fromisoformat(avail) <= t_utc:
            prev = latest.get(date.fromisoformat(obs))
            if prev is None or avail >= prev[0]:
                latest[date.fromisoformat(obs)] = (avail, float(val))
    return sorted((d, v) for d, (_, v) in latest.items())


def usable_utc(key: str, obs: date) -> datetime:
    """§70.6.1 possession time: the first scheduled fetch (g3.timing,
    daily at 22:00:00+00:00 — our own supervisor schedule, not a
    publisher release constant) at or after the publisher's release. This is what
    the deployed bot actually possesses, and what training uses."""
    from g3.timing import t_usable
    return t_usable(RULES[key]["source"].availability_utc(obs))


def pit_view_usable(key: str, t_utc: datetime) -> list[tuple[date, float]]:
    """THE MODEL READER (70.6.1 possession + 70.9.2 provenance
    dispatch): VALUE_EQUIVALENT serves the current archive at t_usable;
    PIT_RECONSTRUCTED serves only the revision store; UNAVAILABLE
    refuses at any timing."""
    if t_utc.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    prov = _provenance().get(key, "")
    if prov == "UNAVAILABLE":
        raise SeriesUnavailable(
            f"{key} is UNAVAILABLE (NOTES 70.9.2): no value equivalence "
            f"and no valid PIT reconstruction - the feature does not "
            f"exist for Trial 1; no substitute is permitted")
    if prov == "PIT_RECONSTRUCTED":
        return pit_view_reconstructed(key, t_utc)
    return [(d, v) for d, v in load_series(key)
            if usable_utc(key, d) <= t_utc]
