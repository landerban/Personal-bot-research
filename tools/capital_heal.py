#!/usr/bin/env python3
"""
Stage 7: does B's 14% config heal at $800? Train diagnostic, ZERO trials.

  python tools/capital_heal.py

Capital is a Config value: doubling it rescales position sizes without
changing signal, universe or ranking, so this is risk sizing on data already
used, not a new strategy.

The question is MECHANISM REPAIR, not performance. At $400 the 14% config
skipped 51.8% of 2024's rebalances and returned 126% drift with a NEGATIVE
demeaned Sharpe. Does $800 fix it?

Pass/fail fixed in NOTES 42.2 before this ran:
    PASS = demeaned Sharpe > 0 AND drift fraction < 50%
           <30% clean | 30-50% healed-but-watch | >=50% unhealed

Does not touch 2024 or the holdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

FLOOR = 5.0


def d(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def window(res):
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return res.timestamps[lo:], np.array(res.equity[lo:])


def measure(res, cfg, label):
    ts, eq = window(res)
    r = metrics.daily_returns(eq)
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks
    notionals = [abs(w) * rb.equity_at_decision
                 for rb in res.rebalances for w in rb.final_weights.values()]
    n = np.array(notionals) if notionals else np.array([np.nan])
    return {
        "label": label, "capital": cfg.initial_capital,
        "vol_target": cfg.vol_target,
        "sharpe": metrics.sharpe(r),
        "realised_vol": metrics.ann_vol(r),
        "vol_shortfall": cfg.vol_target - metrics.ann_vol(r),
        "ann_return": metrics.ann_return(eq),
        "max_dd": float(dd.max()),
        "n_rebalances": len(res.rebalances),
        "n_scheduled": res.n_scheduled,
        "skip_rate": len(res.skips) / max(res.n_scheduled, 1),
        "skips_by_reason": res.skip_counts(),
        "notional_median": float(np.median(n)), "notional_p05": float(np.percentile(n, 5)),
        "notional_min": float(np.min(n)),
        "n_under_floor": int((n < FLOOR).sum()), "n_positions": int(len(n)),
        "lev_median": float(np.median([rb.realised_gross_leverage
                                       for rb in res.rebalances])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    ap.add_argument("--capital", type=float, default=800.0)
    a = ap.parse_args()
    start, end = runner.split_view_range("train")
    assert d(end) == "2023-12-31", f"not the train window: {d(end)}"

    def cfg_for(capital, vol):
        return Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                      max_liquidity_rank=15, vol_target=vol,
                      initial_capital=capital)

    runs = [(a.capital, 0.14, f"B @ 14% @ ${a.capital:.0f}  <- THE TEST"),
            (400.0, 0.14, "B @ 14% @ $400   (broken ref, 39.6)"),
            (400.0, 0.20, "B @ 20% @ $400   (clean-mech ref, 34)")]

    store = PointInTimeStore(a.db, read_only=True)
    dstore = PointInTimeStore(a.demeaned_db, read_only=True)
    rows, drift = [], {}
    for cap, vol, label in runs:
        cfg = cfg_for(cap, vol)
        print(f"running {label} ...", flush=True)
        res = run_backtest(store, cfg, start, end)
        rows.append(measure(res, cfg, label))
        # drift: same config on the demeaned store
        dstore.reset_clock()
        resd = run_backtest(dstore, cfg, start, end)
        _, eqd = window(resd)
        _, eq = window(res)
        sr, srd = metrics.sharpe(metrics.daily_returns(eq)), \
            metrics.sharpe(metrics.daily_returns(eqd))
        drift[label] = {"real": sr, "demeaned": srd, "drift": sr - srd,
                        "fraction": (sr - srd) / sr if sr else float("nan")}
    store.close(); dstore.close()

    print(f"\n=== Stage 7: does the 14% config heal at ${a.capital:.0f}? | train | "
          f"ZERO TRIALS ===\n")
    print(f"{'config':<34} {'skip':>7} {'rebal':>6} {'realised':>9} {'short':>7} "
          f"{'maxDD':>7} {'sharpe':>7} {'lev':>5}")
    for r in rows:
        print(f"{r['label']:<34} {r['skip_rate']:>7.2%} {r['n_rebalances']:>6} "
              f"{r['realised_vol']:>9.2%} {r['vol_shortfall']:>+7.2%} "
              f"{r['max_dd']:>7.2%} {r['sharpe']:>+7.3f} {r['lev_median']:>5.2f}")

    print(f"\n{'config':<34} {'notional med':>13} {'p05':>8} {'min':>8} "
          f"{'under $5':>10} {'of':>8}")
    for r in rows:
        print(f"{r['label']:<34} ${r['notional_median']:>12.2f} "
              f"${r['notional_p05']:>7.2f} ${r['notional_min']:>7.2f} "
              f"{r['n_under_floor']:>10,} {r['n_positions']:>8,}")

    print(f"\n=== DRIFT (the disqualifier, NOTES 42.2) ===")
    print(f"{'config':<34} {'SR real':>8} {'SR demeaned':>12} {'drift':>7} {'fraction':>9}")
    for r in rows:
        dd_ = drift[r["label"]]
        print(f"{r['label']:<34} {dd_['real']:>+8.3f} {dd_['demeaned']:>+12.3f} "
              f"{dd_['drift']:>+7.3f} {dd_['fraction']:>9.0%}")

    test = rows[0]
    td = drift[test["label"]]
    ref20 = rows[2]
    print(f"\n=== VERDICT (NOTES 42.2 / 42.3, fixed before the run) ===")
    pass_drift = td["demeaned"] > 0 and td["fraction"] < 0.50
    band = ("clean (<30%)" if td["fraction"] < 0.30
            else "healed-but-watch (30-50%)" if td["fraction"] < 0.50
            else "UNHEALED (>=50%)")
    print(f"  demeaned Sharpe > 0        : {td['demeaned']:+.3f}   "
          f"{'PASS' if td['demeaned'] > 0 else 'FAIL'}")
    print(f"  drift fraction < 50%       : {td['fraction']:.0%}   "
          f"{'PASS' if td['fraction'] < 0.50 else 'FAIL'}   -> {band}")
    print(f"  skip rate vs 20% ref       : {test['skip_rate']:.2%} vs "
          f"{ref20['skip_rate']:.2%}")
    print(f"  realised vol vs target     : {test['realised_vol']:.2%} vs "
          f"{test['vol_target']:.0%}  ({test['vol_shortfall']:+.2%})")
    print(f"  p05 position notional      : ${test['notional_p05']:.2f}  "
          f"(floor ${FLOOR:.0f})")
    print(f"  positions under the floor  : {test['n_under_floor']:,} of "
          f"{test['n_positions']:,}")

    if pass_drift and td["fraction"] < 0.30 and test["skip_rate"] <= ref20["skip_rate"] + 0.10:
        verdict = f"HEALED at ${a.capital:.0f}"
    elif pass_drift:
        verdict = f"PARTIALLY HEALED at ${a.capital:.0f} -- marginal"
    else:
        verdict = f"NOT HEALED at ${a.capital:.0f}"
    print(f"\n  --> {verdict}")

    if not (pass_drift and td["fraction"] < 0.30):
        # revised capital target computed from the floor, not guessed
        scale = FLOOR / test["notional_p05"] if test["notional_p05"] > 0 else float("nan")
        need_p05 = a.capital * scale
        scale_min = FLOOR / test["notional_min"] if test["notional_min"] > 0 else float("nan")
        need_min = a.capital * scale_min
        print(f"\n  revised capital target, computed from the floor:")
        print(f"    to lift p05 position to ${FLOOR:.0f}: ${need_p05:,.0f}")
        print(f"    to lift the MINIMUM position to ${FLOOR:.0f}: ${need_min:,.0f}")
        print(f"    (linear in capital: positions scale 1:1 with it)")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "capital_heal",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "capital_tested": a.capital,
            "rows": rows, "drift": drift, "verdict": verdict,
            "note": "risk-sizing diagnostic on train; no trial; 2024 not "
                    "re-run; holdout untouched",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=capital_heal)")


if __name__ == "__main__":
    main()
