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
    """Holiday and weekend shifts under publisher timing (NOTES 70.1.1:
    source == publisher release for the re-sourced series): July-4 is
    skipped for the H.15 next-business-day release; Friday observations
    release Monday; Good Friday and Juneteenth (2021+) are in the union
    calendar."""
    t = four_timestamps("fred_DGS2", date(2024, 7, 3))        # Wed pre-4th
    assert t["underlying_public_time"].startswith("2024-07-05"), t
    assert t["source_available_time"].startswith("2024-07-05"), t
    t = four_timestamps("fred_DGS2", date(2024, 7, 12))       # Friday
    assert t["underlying_public_time"].startswith("2024-07-15")
    assert t["source_available_time"].startswith("2024-07-15")  # Monday
    assert date(2024, 3, 29) in us_market_holidays(2024)      # Good Friday
    assert date(2021, 6, 18) in us_market_holidays(2021)      # Juneteenth obs
    assert not any(d.month == 6 and d.day in (18, 19)
                   for d in us_market_holidays(2020))
    print("PASS g3a2_calendar")


def test_underlying_never_after_source_and_fred_mirror_lags():
    """§70.8 mixed rule: source availability EQUALS the publisher
    release only for VERIFIED series (DGS2, DGS10, cboe_VIX); the
    UNVERIFIED series (DTWEXBGS, SP500, NASDAQ100) and the VIXCLS
    mirror cross-check lag strictly. §68.12.2: retrieved_at_utc may be
    null — then the quality flag and a tz-aware upper bound must say
    so."""
    obs = date(2024, 6, 12)
    verified = {"fred_DGS2", "fred_DGS10", "cboe_VIX"}
    for key in RULES:
        t = four_timestamps(key, obs)
        u = datetime.fromisoformat(t["underlying_public_time"])
        s = datetime.fromisoformat(t["source_available_time"])
        if key in verified:
            assert s == u, f"{key}: publisher timing earned (70.8)"
        else:
            assert s > u, f"{key}: must lag the publisher (70.8)"
        if t["retrieved_at_utc"] is None:
            assert t["retrieval_time_quality"] == "upper_bound", key
            b = datetime.fromisoformat(t["retrieved_at_upper_bound_utc"])
            assert b.tzinfo is not None
        else:
            assert t["retrieval_time_quality"] == "observed", key
            r = datetime.fromisoformat(t["retrieved_at_utc"])
            assert r.tzinfo is not None
    print("PASS g3a2_four_timestamps")


def test_manifest_provenance_and_policy_fields():
    m = json.loads(MANIFEST.read_text("utf-8"))
    classes = {"public_domain", "redistribution_restricted", "licensed"}
    adopted = {"cboe_VIX", "fred_DGS2", "fred_DGS10", "fred_DTWEXBGS",
               "fred_SP500", "fred_NASDAQ100"}          # NOTES 70.1.2
    for e in m["series"]:
        assert e["licence_class"] in classes, e["key"]
        if e["key"] in adopted:
            assert e["adopted"] is True, e["key"]
            assert "70.1.2" in e["adoption_decision"]
        else:
            assert e["adopted"] is False, e["key"]      # VIXCLS, gold
        # §68.12.1: the source chain never collapses — publisher and
        # retrieval_source recorded per series, distinct wherever they
        # genuinely are (only CBOE serves its own bytes).
        assert e["publisher"] and e["retrieval_source"], e["key"]
        assert e["availability_resolution"], e["key"]
        if e["key"] == "cboe_VIX":
            assert e["publisher"] == e["retrieval_source"] == "CBOE"
        else:
            assert e["publisher"] != e["retrieval_source"], e["key"]
        if e["key"] == "gold_LBMA":
            assert e["verification_status"] == "UNVERIFIED"
            continue
        # §68.12.2: unknown is not estimated — no commit timestamp in an
        # observed-time field.
        assert e["retrieved_at_utc"] is None, e["key"]
        assert e["retrieval_time_quality"] == "upper_bound", e["key"]
        b = datetime.fromisoformat(e["retrieved_at_upper_bound_utc"])
        assert b.tzinfo is not None
        assert "retrieved_at_provenance" not in e
        if e["key"] in ("fred_DGS2", "fred_DGS10"):
            assert e["source_release_business_day_offset"] == 1, e["key"]
        if e["key"] == "fred_DTWEXBGS":                 # UNVERIFIED (70.8)
            assert e["source_release_business_day_offset"] == 2
        if e["key"] in ("fred_SP500", "fred_NASDAQ100"):  # UNVERIFIED
            assert e["source_release_business_day_offset"] == 1, e["key"]
        assert e["source_release_timezone"] == "America/New_York"
        assert e["source_release_calendar"]
        assert "revision_policy" in e
        # 70.8.1: vintage archives were located for every series except
        # fred_SP500 (no ALFRED vintages pass the honesty guard).
        assert e["vintage_support"] is (e["key"] != "fred_SP500")
        for f in ("http_last_modified", "http_etag", "http_status"):
            assert f in e
    assert "source_chain_rule" in m
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


def test_v3_mixed_timing_enforced_per_series():
    """§70.8: UNVERIFIED series cannot be read at publisher timing —
    the loader's source rule is at least one business day later than
    the publisher release; VERIFIED series earn equality. Checked from
    the manifest, so the enforcement is structural."""
    from tools.g3_exogenous_loader import pit_view_usable, usable_utc
    m = json.loads(MANIFEST.read_text("utf-8"))
    eq = {e["key"]: e.get("publisher_value_equivalence")
          for e in m["series"]}
    days = [date(2024, 1, 8), date(2024, 6, 12), date(2024, 11, 20)]
    for key in RULES:
        if key == "fred_VIXCLS":
            continue                       # cross-check, mirror always
        for d in days:
            u = RULES[key]["underlying"].availability_utc(d)
            src = RULES[key]["source"].availability_utc(d)
            if eq[key] == "VERIFIED":
                assert src == u, (key, d)
            else:
                assert src >= u + timedelta(days=1), (key, d)
    # possession check on a real UNVERIFIED series: at the 22:00Z fetch
    # on the PUBLISHER release date, the observation is NOT possessed.
    obs = date(2024, 6, 12)                # SP500 close published same day
    pub_fetch = usable_utc("fred_DGS2", obs)  # any same-era 22:00Z instant
    pub_day_2200 = datetime(2024, 6, 12, 22, 0, tzinfo=timezone.utc)
    have = {d for d, _ in pit_view_usable("fred_SP500", pub_day_2200)}
    assert obs not in have, "UNVERIFIED series read at publisher timing"
    nxt = datetime(2024, 6, 13, 22, 0, tzinfo=timezone.utc)
    have2 = {d for d, _ in pit_view_usable("fred_SP500", nxt)}
    assert obs in have2
    del pub_fetch
    print("PASS g3v3_mixed_timing")


def test_v3_verification_fields_and_label_split():
    """§70.8: per-series verification block present; the quality label
    split applied; NO series is labelled "observed"; UNVERIFIED series
    carry the conservative timing rule."""
    m = json.loads(MANIFEST.read_text("utf-8"))
    adopted = {"cboe_VIX", "fred_DGS2", "fred_DGS10", "fred_DTWEXBGS",
               "fred_SP500", "fred_NASDAQ100"}
    quality_enum = {"conservative_assumption", "documented_schedule",
                    "observed"}
    for e in m["series"]:
        k = e["key"]
        if k == "gold_LBMA":
            continue
        assert e["publisher_value_equivalence"] in ("VERIFIED",
                                                    "UNVERIFIED"), k
        assert e["verification_method"].strip(), k
        assert e["verification_evidence"].strip(), k
        assert "historical_value_source" in e and "production_source" in e
        assert e["source_availability_quality"] in quality_enum, k
        assert e["source_availability_quality"] != "observed", (
            f"{k}: observed requires actual timestamps (70.8.0)")
        if k in adopted:
            assert e["source_availability_quality"] == "documented_schedule"
            assert e["publication_schedule"].strip()
            assert e["usable_time_rule"].strip()
            if e["publisher_value_equivalence"] == "UNVERIFIED":
                assert "CONSERVATIVE" in e["timing_rule"], k
    assert any("information-age" in r for r in m["open_refinements"])
    print("PASS g3v3_verification_fields")
