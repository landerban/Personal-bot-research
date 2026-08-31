"""
Trial-1 runner dry run (NOTES §65.2.11): the full pipeline end-to-end on a
FULLY SYNTHETIC in-memory store — no real data, no attempt consumed. This
exists to protect the single registered real execution from trivial
crashes; the criteria themselves are tested in their own suites.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.sizing import SymbolFilters  # noqa: E402
from rcm.eval_formation import evaluate as eval_formation  # noqa: E402
from rcm.eval_ic import evaluate as eval_ic  # noqa: E402
from research.trial1.runner import DAY, run  # noqa: E402

H8 = 8 * 3_600_000


def synthetic_store(n_names=20, lead_days=200, era_days=70,
                    era_start=1_577_836_800_000):
    rng = np.random.default_rng(99)
    names = sorted(f"SY{k:02d}USDT" for k in range(n_names))
    first = era_start - lead_days * DAY
    last = era_start + era_days * DAY
    days = list(range(first, last + DAY, DAY))

    beta = 1.0 + 0.2 * rng.standard_normal(n_names)
    closes: dict[str, dict[int, float]] = {}
    r_btc = 0.02 * rng.standard_normal(len(days))
    r_eth = 0.8 * r_btc + 0.012 * rng.standard_normal(len(days))
    for sym, r in (("BTCUSDT", r_btc), ("ETHUSDT", r_eth)):
        px, d = 100.0, {}
        for k, day in enumerate(days):
            px *= 1.0 + r[k]
            d[day] = px
        closes[sym] = d
    for j, sym in enumerate(names):
        drift = 0.0008 * np.sin(j)               # cross-sectional variety
        px, d = 50.0, {}
        for k, day in enumerate(days):
            px *= 1.0 + beta[j] * r_btc[k] + drift + \
                0.015 * rng.standard_normal()
            d[day] = px
        closes[sym] = d

    execs = {s: {day: closes[s][day - DAY] * 1.0001
                 for day in days if day - DAY in closes[s]}
             for s in closes}

    # funding: exact 8h boundaries, name-dependent rates wide enough that
    # the carry book clears the frozen eta and actually FORMS
    fund = {}
    for j, sym in enumerate(names):
        rate = -5e-4 + 2.5e-3 * j / (n_names - 1)
        ts = list(range((first // H8) * H8, last + H8, H8))
        fund[sym] = (tuple(ts), tuple([rate] * len(ts)))

    filters = {s: SymbolFilters(s, 5.0, 0.001) for s in names}
    times = {s: np.array(sorted(closes[s]), dtype=np.int64) for s in closes}
    sets = {s: set(closes[s]) for s in closes}
    return ({"closes": closes, "execs": execs, "fund": fund,
             "filters": filters, "class_ok": names, "times": times,
             "sets": sets, "n_snapped": 0},
            era_start, era_start + (era_days - 1) * DAY)


def test_runner_end_to_end_on_synthetic_store(tmp_path):
    store, t0, t1 = synthetic_store()
    res = run(store, era_start=t0, era_end=t1, out_dir=tmp_path,
              calib_check_every=10)
    n_days = (t1 - t0) // DAY + 1
    assert len(res["day_records"]) == n_days
    assert len(res["daily_rows"]) == n_days
    cats = {r["category"] for r in res["daily_rows"]}
    assert cats <= {"formed", "gate", "structural", "operational",
                    "degenerate"}
    assert sum(res["counts"].values()) == n_days
    assert res["counts"]["formed"] >= 5, (
        f"dry run must exercise the formed path: {res['counts']}")
    assert res["counts"]["operational"] == 0, (
        "no harness error is acceptable on clean synthetic data: "
        + str([r for r in res["daily_rows"]
               if r["category"] == "operational"][:2]))
    assert res["n_calib_equivalence_checks"] >= 1
    # every formed day carries the carry label (mu_mom ~ 0 => carry book)
    formed = [r for r in res["daily_rows"] if r["category"] == "formed"]
    assert all(r["gross_real"] > 0 for r in formed)
    # both evaluators consume the runner's outputs end-to-end
    fv = eval_formation(res["day_records"])
    assert fv.n_windows >= 1
    iv = eval_ic(res["ic_records"], seed=2805281367)
    assert iv.n_defined > 30 and np.isfinite(iv.ic_mean)
    # reporting objects exist and are shaped
    assert "degenerate_rate" in res["reporting_tuple"]
    assert res["delta_gate"].transition_rule.startswith("hold")
    assert (tmp_path / "daily.jsonl").exists()
    print(f"PASS trial1_dryrun (counts {res['counts']}, "
          f"IC n={iv.n_defined}, windows={fv.n_windows}, "
          f"calib checks {res['n_calib_equivalence_checks']})")
