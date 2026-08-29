#!/usr/bin/env python3
"""
Stage 13 Part A: what does the frozen config become at $2,000 and $2,500?

  python tools/capital_tier.py

Train diagnostic, ZERO TRIALS -- capital re-sizing is the Stage 7 class
(NOTES 42/43): it rescales position sizes without touching the signal, the
universe rule or the ranking.

THE QUESTION (NOTES 50.1)
------------------------
The $800/10% train drawdown of 17.03% was measured with 21.55% of days
SKIPPED, and §42.6 showed skips are accidentally protective. Higher capital
heals skips and reveals a truer, larger drawdown. The honest-ratio estimate at
10% vol is ~19.2% against the 20% cap -- inside by 0.8 points, which is inside
this project's measurement noise. So the question is open until measured.

THE READING was fixed in NOTES 50.3 before this ran:
  maxDD <= 20% at both, drift < 30%, demeaned SR > 0, skips collapse
      -> the 10% vol choice CARRIES to the tier
  maxDD > 20%
      -> the coupling bites; the tier's vol must be re-derived by the §43
         three-condition rule, as a FUTURE free sweep, NOT ad hoc here
  drift or skips degrade
      -> something new; stop and report

$800 is re-run ONLY as a determinism check against §43.6 -- if it does not
reproduce, the crypto-filter wiring (NOTES 50.0) moved something and the stage
stops. It is not re-measured and not re-reported as a result.

The $800/10% DEPLOYMENT CONFIG IS UNCHANGED by anything here (NOTES 50.3).
2024 and the holdout are untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

DD_CAP = 0.20
DRIFT_CAP = 0.30
CAPITALS = (800.0, 2000.0, 2500.0)
REFERENCE = 800.0
# NOTES 43.6, the 10% @ $800 train row. The determinism check.
REF800 = {"sharpe": 0.8351, "n_rebalances": 1241, "skip_rate": 0.2155,
          "max_dd": 0.1703, "realised_vol": 0.0928, "demeaned_sharpe": 0.6513}
# Symbols whose MIN_NOTIONAL is above the $5 floor -- the seating question.
WATCH = ("BTCUSDT", "ETHUSDT")


def d(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def window(res):
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    return res.timestamps[max(i - 1, 0):], np.array(res.equity[max(i - 1, 0):])


def measure(res, resd, capital) -> dict:
    ts, eq = window(res)
    r = metrics.daily_returns(eq)
    _, eqd = window(resd)
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks
    sr = metrics.sharpe(r)
    srd = metrics.sharpe(metrics.daily_returns(eqd))
    n = np.array([abs(w) * rb.equity_at_decision
                  for rb in res.rebalances
                  for w in rb.final_weights.values()])

    seated = Counter()
    dropped = Counter()
    for rb in res.rebalances:
        seated.update(rb.final_weights.keys())
        dropped.update(rb.dropped)
    # names the feasibility loop removed on days that then SKIPPED
    skip_dropped = Counter()
    for _, reason, detail in res.skips:
        if reason == "below_min_notional_post_hedge" and " by " in detail:
            skip_dropped.update(detail.split(" by ")[-1].split(","))

    return {
        "capital": capital,
        "sharpe": sr, "demeaned_sharpe": srd,
        "drift_fraction": (sr - srd) / sr if sr else float("nan"),
        "ann_return": metrics.ann_return(eq),
        "realised_vol": metrics.ann_vol(r),
        "vol_shortfall": 0.10 - metrics.ann_vol(r),
        "max_dd": float(dd.max()), "dd_date": d(ts[int(np.argmax(dd))]),
        "n_rebalances": len(res.rebalances),
        "n_scheduled": res.n_scheduled,
        "skip_rate": len(res.skips) / max(res.n_scheduled, 1),
        "skips_by_reason": res.skip_counts(),
        "notional_median": float(np.median(n)),
        "notional_p05": float(np.percentile(n, 5)),
        "notional_min": float(n.min()),
        "seated": dict(seated.most_common(20)),
        "dropped_on_traded_days": dict(dropped.most_common(20)),
        "dropped_on_skipped_days": dict(skip_dropped.most_common(20)),
        "watch": {s: {"seated_days": seated.get(s, 0),
                      "dropped_traded": dropped.get(s, 0),
                      "dropped_skipped": skip_dropped.get(s, 0)}
                  for s in WATCH},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    a = ap.parse_args()

    start, end = runner.split_view_range("train")
    assert d(end) == "2023-12-31", f"not the train window: {d(end)}"

    print("=== Stage 13 Part A: the frozen config at higher capital | train "
          "| ZERO TRIALS ===")
    print(f"    window {d(start)} -> {d(end)}; crypto filter active "
          f"(NOTES 50.0); 2024 and holdout untouched\n")

    store = PointInTimeStore(a.db, read_only=True)
    dstore = PointInTimeStore(a.demeaned_db, read_only=True)
    rows = []
    for cap in CAPITALS:
        cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                     max_liquidity_rank=15, vol_target=0.10,
                     initial_capital=cap, rank_buffer=0)
        print(f"running ${cap:,.0f} ...", flush=True)
        store.reset_clock()
        res = run_backtest(store, cfg, start, end)
        dstore.reset_clock()
        resd = run_backtest(dstore, cfg, start, end)
        rows.append(measure(res, resd, cap))
    store.close()
    dstore.close()

    ref = next(r for r in rows if r["capital"] == REFERENCE)
    print(f"\n=== determinism check: ${REFERENCE:,.0f} must reproduce NOTES 43.6 ===")
    ok = True
    for k, want in REF800.items():
        got = ref[k]
        match = abs(got - want) <= (1 if k == "n_rebalances" else 0.005)
        ok &= match
        print(f"  {k:<18} got {got:>10.4f}   43.6 says {want:>10.4f}   "
              f"{'ok' if match else 'MISMATCH'}")
    if not ok:
        sys.exit("STOP: the $800 row does not reproduce NOTES 43.6 -- the "
                 "crypto-filter wiring moved something (NOTES 50.0). The "
                 "premise is wrong and this stage stops.")

    print(f"\n=== the mechanism table (NOTES 50.2 / STAGE13 A.3) ===")
    print(f"{'capital':>9} {'maxDD':>7} {'DD date':>12} {'skip':>7} {'rebal':>6} "
          f"{'realised':>9} {'short':>7} {'drift':>7} {'demean SR':>10} {'sharpe':>7}")
    for r in rows:
        tag = "  <- reference" if r["capital"] == REFERENCE else ""
        print(f"${r['capital']:>8,.0f} {r['max_dd']:>7.2%} {r['dd_date']:>12} "
              f"{r['skip_rate']:>7.2%} {r['n_rebalances']:>6} "
              f"{r['realised_vol']:>9.2%} {r['vol_shortfall']:>+7.2%} "
              f"{r['drift_fraction']:>7.0%} {r['demeaned_sharpe']:>+10.3f} "
              f"{r['sharpe']:>+7.3f}{tag}")

    print(f"\n{'capital':>9} {'notional med':>13} {'p05':>9} {'min':>9}")
    for r in rows:
        print(f"${r['capital']:>8,.0f} ${r['notional_median']:>12.2f} "
              f"${r['notional_p05']:>8.2f} ${r['notional_min']:>8.2f}")

    print(f"\n=== SEATING: does the book actually become 'majors incl. BTC'? ===")
    print(f"{'capital':>9}  {'symbol':<10} {'seated days':>12} {'dropped(traded)':>16} "
          f"{'dropped(skipped)':>17}")
    for r in rows:
        for s in WATCH:
            w = r["watch"][s]
            print(f"${r['capital']:>8,.0f}  {s:<10} {w['seated_days']:>12,} "
                  f"{w['dropped_traded']:>16,} {w['dropped_skipped']:>17,}")

    print(f"\n=== skips by reason ===")
    for r in rows:
        parts = ", ".join(f"{k}={v}" for k, v in
                          sorted(r["skips_by_reason"].items(), key=lambda kv: -kv[1]))
        print(f"  ${r['capital']:>7,.0f}  {parts or '(none)'}")

    # ---- the reading (NOTES 50.3) ---------------------------------------
    tiers = [r for r in rows if r["capital"] != REFERENCE]
    print(f"\n=== READING (NOTES 50.3, fixed before the run) ===")
    verdicts = {}
    for r in tiers:
        dd_ok = r["max_dd"] <= DD_CAP
        drift_ok = r["drift_fraction"] < DRIFT_CAP and r["demeaned_sharpe"] > 0
        skips_collapsed = r["skip_rate"] < ref["skip_rate"] / 2
        print(f"  ${r['capital']:>7,.0f}  DD {r['max_dd']:.2%} "
              f"{'<=' if dd_ok else '>'} {DD_CAP:.0%}   "
              f"drift {r['drift_fraction']:.0%} demeaned {r['demeaned_sharpe']:+.3f} "
              f"{'ok' if drift_ok else 'FAIL'}   "
              f"skip {r['skip_rate']:.2%} vs {ref['skip_rate']:.2%} "
              f"{'collapsed' if skips_collapsed else 'NOT collapsed'}")
        verdicts[r["capital"]] = {"dd_ok": dd_ok, "drift_ok": drift_ok,
                                  "skips_collapsed": skips_collapsed}

    all_dd_ok = all(v["dd_ok"] for v in verdicts.values())
    all_drift_ok = all(v["drift_ok"] for v in verdicts.values())
    all_skips = all(v["skips_collapsed"] for v in verdicts.values())

    if all_dd_ok and all_drift_ok and all_skips:
        branch = ("ONE: the 10% vol choice CARRIES to this tier. Drawdown "
                  "stays inside the 20% cap with skips healed, the mechanism "
                  "stays clean, and the book finally includes the large-"
                  "MIN_NOTIONAL majors. Record as the config-of-record FOR "
                  "THAT CAPITAL TIER.")
    elif not all_dd_ok:
        breach = [f"${c:,.0f} {verdicts[c]}" for c in verdicts
                  if not verdicts[c]["dd_ok"]]
        branch = ("TWO: the coupling BITES. With skips healed, 10% vol "
                  f"breaches the 20% cap ({', '.join(breach)}). The vol for "
                  "this tier must be re-derived by the §43 three-condition "
                  "rule as a FUTURE FREE SWEEP -- NOT done here (NOTES 50.5).")
    else:
        branch = ("THREE: drift or skips degraded -- something new. STOP and "
                  "report (NOTES 50.3).")
    print(f"\n  --> BRANCH {branch}")
    print(f"\n  The $800/10% DEPLOYMENT CONFIG IS UNCHANGED (NOTES 50.3): it "
          f"was validated as-is, skips and all. This characterises a different "
          f"capital tier and re-freezes nothing.")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "capital_tier",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "capitals": list(CAPITALS),
            "dd_cap": DD_CAP, "drift_cap": DRIFT_CAP,
            "rows": rows, "verdicts": {str(k): v for k, v in verdicts.items()},
            "branch": branch,
            "note": "risk-sizing diagnostic on train; no trial; 2024 not run; "
                    "holdout untouched; $800 row is a determinism check only",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=capital_tier)")


if __name__ == "__main__":
    main()
