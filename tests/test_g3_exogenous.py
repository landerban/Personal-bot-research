"""
Stage G3-A2 (NOTES §68.11): the PIT-hazard and source-policy corrections.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.g3_exogenous_loader import (  # noqa: E402
    RULES, four_timestamps, pit_view, us_market_holidays,
)

MANIFEST = ROOT / "data" / "exogenous" / "MANIFEST.json"
UTC_CONST = re.compile(r"\d{1,2}:\d{2}\s*(?:Z\b|UTC)")


def test_no_utc_release_constant_anywhere():
    """§68.11.1.1 grep test: manifest, loader, downloader."""
    for f in (MANIFEST, ROOT / "tools" / "g3_exogenous_loader.py",
              ROOT / "tools" / "g3a_exogenous_download.py"):
        txt = f.read_text(encoding="utf-8")
        assert not UTC_CONST.search(txt), f"{f.name} carries a UTC constant"
        for lit in ("20:15", "21:15", "20:00", "20:30"):
            assert lit not in txt, f"{f.name} contains {lit}"
    print("PASS g3a2_no_utc_constants")


def test_january_and_july_resolve_to_different_utc_instants():
    """§68.11.1.1: DST means the same local release maps to two different
    UTC offsets across the year — asserted without writing either literal."""
    jan = four_timestamps("fred_DGS2", date(2024, 1, 10))
    jul = four_timestamps("fred_DGS2", date(2024, 7, 10))
    tj = datetime.fromisoformat(jan["source_available_time"])
    ts = datetime.fromisoformat(jul["source_available_time"])
    assert tj.tzinfo is not None and ts.tzinfo is not None
    mins = lambda t: t.hour * 60 + t.minute  # noqa: E731
    assert abs(mins(tj) - mins(ts)) == 60, (
        "winter and summer releases must differ by exactly the DST hour "
        f"in UTC ({tj.timetz()} vs {ts.timetz()})")
    print(f"PASS g3a2_dst ({tj.timetz()} vs {ts.timetz()})")


def test_access_rule_source_available_time_governs():
    """§68.11.1.2: a value is invisible one minute before its source served
    it and visible one minute after. Dev-era dates only."""
    obs = date(2024, 7, 10)                       # Wednesday
    avail = datetime.fromisoformat(
        four_timestamps("fred_DGS2", obs)["source_available_time"])
    before = pit_view("fred_DGS2", avail - timedelta(minutes=1))
    after = pit_view("fred_DGS2", avail + timedelta(minutes=1))
    assert all(d < obs for d, _ in before)
    assert any(d == obs for d, _ in after)
    with pytest.raises(ValueError, match="timezone-aware"):
        pit_view("fred_DGS2", datetime(2024, 7, 12))
    print("PASS g3a2_access_rule")


def test_calendar_shifts_are_conservative_and_correct():
    """Holiday and weekend shifts: July-4 skipped for the next-business-day
    release; Friday observations release Monday; Good Friday and Juneteenth
    (2021+) are in the union calendar."""
    t = four_timestamps("fred_DGS2", date(2024, 7, 3))
    assert t["source_available_time"].startswith("2024-07-05"), t
    t = four_timestamps("fred_DGS2", date(2024, 7, 12))       # Friday
    assert t["source_available_time"].startswith("2024-07-15")  # Monday
    assert date(2024, 3, 29) in us_market_holidays(2024)      # Good Friday
    assert date(2021, 6, 18) in us_market_holidays(2021)      # Juneteenth obs
    assert not any(d.month == 6 and d.day in (18, 19)
                   for d in us_market_holidays(2020))
    print("PASS g3a2_calendar")


def test_underlying_never_after_source_and_fred_mirror_lags():
    """§68.11.1.2: source_available_time >= underlying_public_time for every
    staged series; strictly later for the FRED-mirrored cash indices."""
    obs = date(2024, 6, 12)
    for key in RULES:
        t = four_timestamps(key, obs)
        u = datetime.fromisoformat(t["underlying_public_time"])
        s = datetime.fromisoformat(t["source_available_time"])
        assert s >= u, key
        if key in ("fred_SP500", "fred_NASDAQ100", "fred_VIXCLS"):
            assert s > u, f"{key}: the mirror must lag the underlying"
        r = datetime.fromisoformat(t["retrieved_at_utc"])
        assert r.tzinfo is not None
    print("PASS g3a2_four_timestamps")


def test_manifest_provenance_and_policy_fields():
    m = json.loads(MANIFEST.read_text("utf-8"))
    classes = {"public_domain", "redistribution_restricted", "licensed"}
    for e in m["series"]:
        assert e["licence_class"] in classes, e["key"]
        assert e["adopted"] is False
        if e["key"] == "gold_LBMA":
            assert e["verification_status"] == "UNVERIFIED"
            continue
        assert e["source_release_timezone"] == "America/New_York"
        assert e["source_release_calendar"]
        assert "revision_policy" in e and e["vintage_support"] is False
        for f in ("http_last_modified", "http_etag", "http_status"):
            assert f in e
    assert len(m["adoption_decisions_open"]) >= 3
    assert "raw_data_policy" in m and "panel_split" in m
    assert "revisable_series_rule" in m
    assert any(e["key"] == "gold_LBMA" for e in m["series"])
    print("PASS g3a2_manifest_schema")


def test_restricted_raw_files_are_untracked_and_ignored():
    """§68.11.3: only the manifest and the public-domain Fed series are
    tracked; the restricted CSVs live untracked in raw/."""
    out = subprocess.run(["git", "ls-files", "data/exogenous"],
                         capture_output=True, text=True, cwd=ROOT)
    tracked = set(Path(x).name for x in out.stdout.split())
    assert tracked == {"MANIFEST.json", "fred_DGS2.csv", "fred_DGS10.csv",
                       "fred_DTWEXBGS.csv"}, tracked
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/exogenous/raw/" in gi
    for name in ("fred_SP500.csv", "fred_NASDAQ100.csv", "cboe_VIX.csv",
                 "fred_VIXCLS.csv"):
        assert (ROOT / "data" / "exogenous" / "raw" / name).exists(), (
            f"{name} must still exist locally — untracked is not deleted")
    print("PASS g3a2_licence_policy")
