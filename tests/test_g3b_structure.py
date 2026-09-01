"""
Stage G3-B (NOTES 69.0/69.1): Part 0 invariants and the measurement
mechanics — synthetic data only; the single real execution is 69.2's.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import research.g3b_structure as g              # noqa: E402
from rcm.seal import SealViolation              # noqa: E402
from tools.g3_exogenous_loader import (         # noqa: E402
    RULES, ReleaseRule, pit_view,
)

MANIFEST = ROOT / "data" / "exogenous" / "MANIFEST.json"
STAGED = ("fred_DGS2", "fred_DGS10", "fred_DTWEXBGS", "cboe_VIX",
          "fred_VIXCLS", "fred_SP500", "fred_NASDAQ100")


# ---------------------------------------------------------------- Part 0

def test_manifest_availability_quality_fields():
    """69.0: quality/basis recorded for all seven staged series; only the
    two admissible quality values; gold_LBMA has neither until procured."""
    m = json.loads(MANIFEST.read_text("utf-8"))
    by_key = {e["key"]: e for e in m["series"]}
    for key in STAGED:
        e = by_key[key]
        assert e["source_availability_quality"] == "conservative_assumption"
        assert e["source_availability_quality"] in (
            "conservative_assumption", "observed")
        assert e["source_availability_basis"].strip(), key
    assert "source_availability_quality" not in by_key["gold_LBMA"]
    assert "source_availability_basis" not in by_key["gold_LBMA"]
    print("PASS g3b_part0_quality_fields")


def test_invariant_source_never_before_underlying_full_year_sweep():
    """69.0 invariant 1, swept: every staged series, every calendar date
    of 2024 (leap year, both DST transitions, holidays) plus the 2021
    Juneteenth window — source availability never precedes underlying."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(366)]
    days += [date(2021, 6, 14) + timedelta(days=i) for i in range(10)]
    for key, r in RULES.items():
        for d in days:
            u = r["underlying"].availability_utc(d)
            s = r["source"].availability_utc(d)
            assert s >= u, (key, d)
    print("PASS g3b_part0_invariant_sweep")


def test_invariant_pit_reader_boundary_exact():
    """69.0 invariant 2 at the exact boundary: an observation is returned
    AT its source_available_time and not one second before."""
    obs = date(2024, 7, 10)                                  # Wednesday
    avail = RULES["fred_DGS2"]["source"].availability_utc(obs)
    at = {d for d, _ in pit_view("fred_DGS2", avail)}
    before = {d for d, _ in pit_view("fred_DGS2",
                                     avail - timedelta(seconds=1))}
    assert obs in at
    assert obs not in before
    print("PASS g3b_part0_boundary")


# ------------------------------------------------------------- refusals

def test_g3b_refusals():
    """69.1.6: the dev-window cap refuses 2025-01-01, and both refusal
    exercises return their messages (seal + module cap)."""
    with pytest.raises(g.RangeRefused, match="2025-01-01"):
        g._refuse_if_late(date(2025, 1, 1))
    g._refuse_if_late(date(2024, 12, 31))       # last legal date passes
    msgs = g.exercise_refusals()
    assert len(msgs) == 2
    assert msgs[0].startswith("SealViolation:")
    assert msgs[1].startswith("RangeRefused:")
    with pytest.raises(SealViolation):
        g.assert_range_allowed(g.DEV_START_MS, g.DEV_END_MS + g.DAY_MS)
    print("PASS g3b_refusals")


# ----------------------------------------------- stale-carry mechanics

def _noon_ny(offset: int) -> ReleaseRule:
    return ReleaseRule("America/New_York", 12, 0, offset, "test")


def test_stale_carry_and_availability_gate(monkeypatch):
    """69.1.3 stale-carry on planted observations: a value is invisible
    before its availability instant under the CHOSEN timing rule, carried
    days have r=0 and stale=1, and yield series difference in levels."""
    obs = [(date(2020, 1, 2), 1.50), (date(2020, 1, 3), 1.75),
           (date(2020, 1, 6), 1.60)]
    monkeypatch.setitem(g.RULES, "fred_DGS2",
                        {"underlying": _noon_ny(0), "source": _noon_ny(1)})
    monkeypatch.setattr(g, "load_series", lambda key: obs)
    i = {d: (d - g.DEV_START).days for d in
         [date(2020, 1, j) for j in range(1, 10)]}

    r_src, st_src = g.exogenous_returns("fred_DGS2", "source")
    assert np.isnan(r_src[i[date(2020, 1, 2)]])       # not yet available
    assert np.isnan(st_src[i[date(2020, 1, 2)]])
    assert st_src[i[date(2020, 1, 3)]] == 0           # first arrival
    assert st_src[i[date(2020, 1, 4)]] == 1           # carried
    assert r_src[i[date(2020, 1, 4)]] == 0.0
    # obs 01-03 arrives Mon 01-06 under offset 1: diff in LEVELS
    assert abs(r_src[i[date(2020, 1, 6)]] - (1.75 - 1.50)) < 1e-12
    assert abs(r_src[i[date(2020, 1, 7)]] - (1.60 - 1.75)) < 1e-12

    r_und, st_und = g.exogenous_returns("fred_DGS2", "underlying")
    assert st_und[i[date(2020, 1, 2)]] == 0           # same-day timing
    assert abs(r_und[i[date(2020, 1, 3)]] - 0.25) < 1e-12
    print("PASS g3b_stale_carry")


def test_log_return_convention_for_non_yield_series(monkeypatch):
    obs = [(date(2020, 1, 2), 100.0), (date(2020, 1, 3), 110.0)]
    monkeypatch.setitem(g.RULES, "fred_SP500",
                        {"underlying": _noon_ny(0), "source": _noon_ny(0)})
    monkeypatch.setattr(g, "load_series", lambda key: obs)
    r, _ = g.exogenous_returns("fred_SP500", "source")
    idx = (date(2020, 1, 3) - g.DEV_START).days
    assert abs(r[idx] - np.log(1.1)) < 1e-12
    print("PASS g3b_log_convention")


# ------------------------------------------------- windowed statistics

def test_planted_shift_recovered_by_the_right_k():
    """Mechanics only, synthetic: if r_X at date t equals r_BTC at t+2,
    the correlation is 1 exactly at k=+2 and materially lower elsewhere."""
    rng = np.random.default_rng(69)
    n = g.N_DATES
    r_btc = rng.standard_normal(n)
    r_x = np.full(n, np.nan)
    r_x[:-2] = r_btc[2:]
    m = g.measure(r_btc, r_x, np.zeros(n))
    v2 = m["rho_p"][2][~np.isnan(m["rho_p"][2])]
    assert len(v2) and np.allclose(v2, 1.0)
    for k in (-2, 0, 3):
        vk = m["rho_p"][k][~np.isnan(m["rho_p"][k])]
        assert np.nanmax(np.abs(vk)) < 0.6
    s2 = m["rho_s"][2][~np.isnan(m["rho_s"][2])]
    assert np.allclose(s2, 1.0)
    print("PASS g3b_shift_mechanics")


def test_spearman_average_ranks_and_ties():
    ranks = g.avg_ranks_rows(np.array([[3.0, 1.0, 1.0, 2.0]]))
    assert np.allclose(ranks[0], [4.0, 1.5, 1.5, 3.0])
    # monotone nonlinear map: Spearman 1, Pearson below 1
    rng = np.random.default_rng(7)
    a = rng.standard_normal(g.WINDOW + 40)
    b = np.exp(3 * a)
    m = g.measure(np.pad(b, (0, g.N_DATES - len(b)),
                         constant_values=np.nan),
                  np.pad(a, (0, g.N_DATES - len(a)),
                         constant_values=np.nan), np.zeros(g.N_DATES))
    s0 = m["rho_s"][0][~np.isnan(m["rho_s"][0])]
    p0 = m["rho_p"][0][~np.isnan(m["rho_p"][0])]
    assert np.allclose(s0, 1.0) and np.all(p0 < 0.999)
    print("PASS g3b_spearman")


def test_beta_matches_closed_form():
    rng = np.random.default_rng(11)
    n = g.WINDOW
    x = rng.standard_normal(g.N_DATES)
    y = 0.7 * x + 0.1 * rng.standard_normal(g.N_DATES)
    m = g.measure(y, x, np.zeros(g.N_DATES))
    w = 100                                       # any fully-formed window
    xs, ys = x[w:w + n], y[w:w + n]
    slope = np.polyfit(xs, ys, 1)[0]
    assert abs(m["beta"][w] - slope) < 1e-10
    resid = ys - np.polyval(np.polyfit(xs, ys, 1), xs)
    se = np.sqrt((resid @ resid) / (n - 2) / ((xs - xs.mean()) ** 2).sum())
    assert abs(m["se"][w] - se) < 1e-10
    print("PASS g3b_beta")


# ------------------------------------------------------ emitted output

OUT = ROOT / "research" / "g3b" / "out"


@pytest.mark.skipif(not (OUT / "diagnostics.jsonl").exists(),
                    reason="69.2 not yet executed")
def test_output_headers_labels_and_dev_window_edges():
    """The artifact itself: headers state the quarantine and no-claims;
    every sensitivity record carries the frozen label; no k=+5 window-end
    later than 2024-12-26 and none earlier than dev start + 89 days."""
    diag = (OUT / "diagnostics.jsonl").read_text("utf-8").splitlines()
    sens = (OUT / "sensitivity.jsonl").read_text("utf-8").splitlines()
    h = json.loads(diag[0])
    assert h["header"] and h["quarantined_from_feature_selection"]
    assert h["trial_consumed"] is False
    assert "Q1-Q4" in h["skill_claim"]
    hs = json.loads(sens[0])
    assert hs["label"] == g.SENSITIVITY_LABEL
    last_k5 = None
    for line in diag[1:]:
        rec = json.loads(line)
        if rec.get("summary"):
            continue
        t = date.fromisoformat(rec["t"])
        assert date(2020, 3, 30) <= t <= date(2024, 12, 31)
        if "5" in rec["rho_pearson"]:
            last_k5 = max(last_k5 or t, t)
    assert last_k5 == date(2024, 12, 26)          # +5 needs BTC through t+5
    for line in sens[1:]:
        rec = json.loads(line)
        if not rec.get("summary"):
            assert rec["label"] == g.SENSITIVITY_LABEL
    print("PASS g3b_artifact_pins")
