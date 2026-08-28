#!/usr/bin/env python3
"""
Stage 7a: the honest vol sweep at $800. Train diagnostic, ZERO trials.

  python tools/honest_vol_sweep.py

Re-derives B's deployment vol on UNCONTAMINATED drawdown numbers. The §39
rule picked 14% from a 14.78% drawdown that was a $400 floor artifact -- the
book skipped the days it would have lost on. At $800 the same vol gives
24.79%.

Selection rule fixed in NOTES 43.3 before this ran -- the highest vol
satisfying ALL THREE simultaneously:

  1. measured max drawdown <= 20%
  2. drift fraction < 30% AND demeaned Sharpe > 0
  3. skip rate <= 22.26% AND realised vol within 2 points of target

Sharpe is reported and NEVER selected on. No condition is relaxed to
manufacture a winner. N and k are unchanged. 2024 and the holdout untouched.
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
DD_CAP = 0.20
DRIFT_CAP = 0.30
SKIP_CAP = 0.1726 + 0.05      # the 42 $800/14% rate + 5 points
VOL_TOL = 0.02
SWEEP = (0.08, 0.10, 0.11, 0.12, 0.14)
# 42's $800/14% row, for the determinism check
REF14 = {"skip_rate": 0.1726, "n_rebalances": 1309, "max_dd": 0.2479,
         "realised_vol": 0.1288, "drift_fraction": 0.15}


def d(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def window(res):
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return res.timestamps[lo:], np.array(res.equity[lo:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    ap.add_argument("--capital", type=float, default=800.0)
    a = ap.parse_args()
    start, end = runner.split_view_range("train")
    assert d(end) == "2023-12-31", f"not the train window: {d(end)}"

    store = PointInTimeStore(a.db, read_only=True)
    dstore = PointInTimeStore(a.demeaned_db, read_only=True)
    rows = []
    for vol in SWEEP:
        cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                     max_liquidity_rank=15, vol_target=vol,
                     initial_capital=a.capital)
        print(f"running vol={vol:.0%} @ ${a.capital:.0f} ...", flush=True)
        res = run_backtest(store, cfg, start, end)
        dstore.reset_clock()
        resd = run_backtest(dstore, cfg, start, end)
        ts, eq = window(res)
        _, eqd = window(resd)
        r = metrics.daily_returns(eq)
        peaks = np.maximum.accumulate(eq)
        dd = (peaks - eq) / peaks
        j = int(np.argmax(dd))
        sr = metrics.sharpe(r)
        srd = metrics.sharpe(metrics.daily_returns(eqd))
        n = np.array([abs(w) * rb.equity_at_decision
                      for rb in res.rebalances for w in rb.final_weights.values()])
        rows.append({
            "vol_target": vol, "sharpe": sr, "demeaned_sharpe": srd,
            "drift": sr - srd, "drift_fraction": (sr - srd) / sr if sr else float("nan"),
            "realised_vol": metrics.ann_vol(r),
            "vol_shortfall": vol - metrics.ann_vol(r),
            "max_dd": float(dd.max()), "dd_date": d(ts[j]),
            "skip_rate": len(res.skips) / max(res.n_scheduled, 1),
            "skips_by_reason": res.skip_counts(),
            "n_rebalances": len(res.rebalances),
            "notional_median": float(np.median(n)),
            "notional_p05": float(np.percentile(n, 5)),
            "notional_min": float(n.min()),
            "n_under_floor": int((n < FLOOR).sum()), "n_positions": int(len(n)),
            "ann_return": metrics.ann_return(eq),
        })
    store.close(); dstore.close()

    # ---- determinism check against 42 ----------------------------------
    r14 = next(r for r in rows if r["vol_target"] == 0.14)
    print(f"\n=== determinism check: 14% must reproduce NOTES 42 ===")
    ok = True
    for k, want in REF14.items():
        got = r14[k]
        match = abs(got - want) <= (1 if k == "n_rebalances" else 0.005)
        ok &= match
        print(f"  {k:<18} got {got:>10.4f}   42 says {want:>10.4f}   "
              f"{'ok' if match else 'MISMATCH'}")
    if not ok:
        sys.exit("STOP: the 14% row does not reproduce NOTES 42 -- something is "
                 "non-deterministic (STAGE7A 2)")

    print(f"\n=== Stage 7a: honest vol sweep @ ${a.capital:.0f} | train | ZERO TRIALS ===")
    print(f"{'vol':>5} {'maxDD':>7} {'DD date':>12} {'skip':>7} {'rebal':>6} "
          f"{'realised':>9} {'short':>7} {'drift':>7} {'demean SR':>10} {'sharpe':>7}")
    for r in rows:
        print(f"{r['vol_target']:>5.0%} {r['max_dd']:>7.2%} {r['dd_date']:>12} "
              f"{r['skip_rate']:>7.2%} {r['n_rebalances']:>6} "
              f"{r['realised_vol']:>9.2%} {r['vol_shortfall']:>+7.2%} "
              f"{r['drift_fraction']:>7.0%} {r['demeaned_sharpe']:>+10.3f} "
              f"{r['sharpe']:>+7.3f}")

    print(f"\n{'vol':>5} {'notional med':>13} {'p05':>8} {'min':>8} {'under $5':>10} {'of':>8}")
    for r in rows:
        print(f"{r['vol_target']:>5.0%} ${r['notional_median']:>12.2f} "
              f"${r['notional_p05']:>7.2f} ${r['notional_min']:>7.2f} "
              f"{r['n_under_floor']:>10,} {r['n_positions']:>8,}")

    print(f"\nskips by reason:")
    for r in rows:
        parts = ", ".join(f"{k}={v}" for k, v in
                          sorted(r["skips_by_reason"].items(), key=lambda kv: -kv[1]))
        print(f"  {r['vol_target']:>4.0%}  {parts or '(none)'}")

    # ---- apply the rule -------------------------------------------------
    print(f"\n=== SELECTION (NOTES 43.3, all three at once) ===")
    print(f"  1. maxDD <= {DD_CAP:.0%}   2. drift < {DRIFT_CAP:.0%} and demeaned SR > 0"
          f"   3. skip <= {SKIP_CAP:.2%} and vol within {VOL_TOL:.0%} of target")
    qualifying = []
    for r in rows:
        c1 = r["max_dd"] <= DD_CAP
        c2 = r["drift_fraction"] < DRIFT_CAP and r["demeaned_sharpe"] > 0
        c3 = r["skip_rate"] <= SKIP_CAP and r["vol_shortfall"] <= VOL_TOL
        fails = []
        if not c1: fails.append(f"DD {r['max_dd']:.2%}>{DD_CAP:.0%}")
        if not c2: fails.append(
            f"drift {r['drift_fraction']:.0%}" +
            ("" if r["demeaned_sharpe"] > 0 else f"/demeaned SR {r['demeaned_sharpe']:+.2f}"))
        if not c3: fails.append(
            (f"skip {r['skip_rate']:.2%}" if r["skip_rate"] > SKIP_CAP else "") +
            (f" vol short {r['vol_shortfall']:+.2%}" if r["vol_shortfall"] > VOL_TOL else ""))
        allok = c1 and c2 and c3
        print(f"  {r['vol_target']:>4.0%}  "
              f"{'1 ok' if c1 else '1 NO'}  {'2 ok' if c2 else '2 NO'}  "
              f"{'3 ok' if c3 else '3 NO'}   "
              f"{'ALL THREE' if allok else 'fails: ' + '; '.join(f for f in fails if f.strip())}")
        if allok:
            qualifying.append(r)

    print()
    if qualifying:
        chosen = max(qualifying, key=lambda r: r["vol_target"])
        outcome = "A"
        verdict = (f"OUTCOME A -- a clean vol exists. DEPLOYMENT VOL = "
                   f"{chosen['vol_target']:.0%} @ ${a.capital:.0f} "
                   f"(maxDD {chosen['max_dd']:.2%}, drift "
                   f"{chosen['drift_fraction']:.0%}, skip {chosen['skip_rate']:.2%}, "
                   f"realised vol {chosen['realised_vol']:.2%})")
    else:
        chosen = None
        cap_ok = [r for r in rows if r["max_dd"] <= DD_CAP]
        floor_clean = [r for r in rows
                       if r["drift_fraction"] < DRIFT_CAP and r["demeaned_sharpe"] > 0
                       and r["skip_rate"] <= SKIP_CAP and r["vol_shortfall"] <= VOL_TOL]
        if cap_ok and floor_clean:
            outcome = "B"
            verdict = (f"OUTCOME B -- the sets do not intersect. Vols meeting the "
                       f"drawdown cap: {[f'{r['vol_target']:.0%}' for r in cap_ok]}; "
                       f"vols that are floor-clean: "
                       f"{[f'{r['vol_target']:.0%}' for r in floor_clean]}. "
                       f"$800 cannot support a cap-satisfying book.")
        elif floor_clean and not cap_ok:
            outcome = "C"
            verdict = (f"OUTCOME C -- every floor-clean vol breaches the drawdown "
                       f"cap. At ${a.capital:.0f} you may have drawdown headroom OR "
                       f"a floor-clean mechanism, not both. A knowing risk decision, "
                       f"deferred to the user.")
        else:
            outcome = "B"
            verdict = (f"OUTCOME B -- no vol is floor-clean at ${a.capital:.0f}. "
                       f"More capital is required.")
    print(f"  --> {verdict}")

    if chosen is None:
        # true joint target: capital that lifts the cap-satisfying vol clear of the floor
        cap_ok = [r for r in rows if r["max_dd"] <= DD_CAP]
        if cap_ok:
            best = max(cap_ok, key=lambda r: r["vol_target"])
            scale = FLOOR / best["notional_p05"] if best["notional_p05"] > 0 else float("nan")
            print(f"\n  joint capital target, computed from the floor:")
            print(f"    the highest cap-satisfying vol is {best['vol_target']:.0%} "
                  f"(maxDD {best['max_dd']:.2%})")
            print(f"    its p05 position at ${a.capital:.0f} is "
                  f"${best['notional_p05']:.2f}; lifting that to ${FLOOR:.0f} needs "
                  f"${a.capital * scale:,.0f}")
            print(f"    NOTE this is necessary, not sufficient -- skip and drift "
                  f"must be re-measured at that capital (positions scale 1:1, "
                  f"but which rebalances clear the floor does not)")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "honest_vol_sweep",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "capital": a.capital,
            "dd_cap": DD_CAP, "drift_cap": DRIFT_CAP, "skip_cap": SKIP_CAP,
            "rows": rows, "outcome": outcome, "verdict": verdict,
            "chosen_vol": chosen["vol_target"] if chosen else None,
            "note": "risk-sizing diagnostic on train; no trial; 2024 not re-run; "
                    "holdout untouched",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=honest_vol_sweep)")


if __name__ == "__main__":
    main()
