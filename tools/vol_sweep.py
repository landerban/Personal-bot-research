#!/usr/bin/env python3
"""
Stage 6a: vol-target sweep for B on train. Risk sizing, ZERO trials.

  python tools/vol_sweep.py

Vol targeting rescales identical positions -- same signal, same universe,
same names -- so this searches for no edge and consumes no trial. What it
decides is the vol at which B is DEPLOYED.

Selection rule, fixed in NOTES 39 before this ran: deploy the HIGHEST vol
target whose MEASURED max drawdown <= 20%, unless disqualified by the floor
(skip rate >10 points above the 20% run, or realised vol >2 points short of
target).

The primary output is the MIN_NOTIONAL floor diagnostics, not Sharpe. A vol
that only 'works' by skipping half its rebalances is not runnable.

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

DD_CAP = 0.20
SKIP_TOLERANCE = 0.10       # points above the 20%-vol run
VOL_SHORTFALL = 0.02        # points below target
SWEEP = (0.12, 0.13, 0.14, 0.15)
REFERENCE = 0.20


def d(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def window(res):
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return res.timestamps[lo:], np.array(res.equity[lo:])


def measure(res, cfg):
    ts, eq = window(res)
    r = metrics.daily_returns(eq)
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks
    j = int(np.argmax(dd))
    notionals = []
    dropped = []
    for rb in res.rebalances:
        for w in rb.final_weights.values():
            notionals.append(abs(w) * rb.equity_at_decision)
        dropped.append(cfg.n_positions - len(rb.final_weights))
    n_sched = max(res.n_scheduled, 1)
    return {
        "vol_target": cfg.vol_target,
        "sharpe": metrics.sharpe(r),
        "realised_vol": metrics.ann_vol(r),
        "ann_return": metrics.ann_return(eq),
        "max_dd": float(dd.max()),
        "dd_date": d(ts[j]),
        "skip_rate": len(res.skips) / n_sched,
        "skips_by_reason": res.skip_counts(),
        "n_rebalances": len(res.rebalances),
        "dropped_per_rebalance": float(np.mean(dropped)) if dropped else float("nan"),
        "n_rebalances_with_drop": int(sum(1 for x in dropped if x > 0)),
        "notional_median": float(np.median(notionals)) if notionals else float("nan"),
        "notional_p95": float(np.percentile(notionals, 95)) if notionals else float("nan"),
        "notional_min": float(np.min(notionals)) if notionals else float("nan"),
        "lev_median": float(np.median([rb.realised_gross_leverage
                                       for rb in res.rebalances])),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()
    start, end = runner.split_view_range("train")
    assert d(end) == "2023-12-31", f"not the train window: {d(end)}"

    store = PointInTimeStore(a.db, read_only=True)
    rows = []
    for v in list(SWEEP) + [REFERENCE]:
        cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                     max_liquidity_rank=15, vol_target=v)
        print(f"running vol_target={v:.0%}...", flush=True)
        rows.append(measure(run_backtest(store, cfg, start, end), cfg))
    store.close()
    ref = rows[-1]

    print(f"\n=== Stage 6a: vol sweep for B | train 2020-2023 | ZERO TRIALS ===")
    print(f"{'vol':>5} {'sharpe':>7} {'realised':>9} {'shortfall':>10} {'ann ret':>9} "
          f"{'max DD':>8} {'DD date':>12} {'skip rate':>10} {'lev':>5}")
    for r in rows:
        tag = "  <- reference (34)" if r["vol_target"] == REFERENCE else ""
        print(f"{r['vol_target']:>5.0%} {r['sharpe']:>+7.3f} {r['realised_vol']:>9.2%} "
              f"{r['vol_target']-r['realised_vol']:>+10.2%} {r['ann_return']:>+9.2%} "
              f"{r['max_dd']:>8.2%} {r['dd_date']:>12} {r['skip_rate']:>10.2%} "
              f"{r['lev_median']:>5.2f}{tag}")

    print(f"\n=== the MIN_NOTIONAL floor -- the primary output (NOTES 39.2) ===")
    print(f"{'vol':>5} {'rebal':>6} {'names dropped/rb':>17} {'rb with a drop':>15} "
          f"{'notional med':>13} {'p95':>8} {'min':>8}")
    for r in rows:
        print(f"{r['vol_target']:>5.0%} {r['n_rebalances']:>6} "
              f"{r['dropped_per_rebalance']:>17.3f} "
              f"{r['n_rebalances_with_drop']:>15} "
              f"${r['notional_median']:>12.2f} ${r['notional_p95']:>7.2f} "
              f"${r['notional_min']:>7.2f}")

    print(f"\nskips by reason:")
    for r in rows:
        parts = ", ".join(f"{k}={v}" for k, v in
                          sorted(r["skips_by_reason"].items(), key=lambda kv: -kv[1]))
        print(f"  {r['vol_target']:>4.0%}  {parts or '(none)'}")

    # ---------------- apply the rule mechanically ------------------------
    print(f"\n=== SELECTION (NOTES 39.1/39.3, fixed before the run) ===")
    print(f"  cap: measured max DD <= {DD_CAP:.0%}   "
          f"disqualify: skip rate > ref+{SKIP_TOLERANCE:.0%} or realised vol "
          f"> {VOL_SHORTFALL:.0%} short")
    qualifying = []
    for r in rows:
        if r["vol_target"] == REFERENCE:
            continue
        dd_ok = r["max_dd"] <= DD_CAP
        skip_ok = r["skip_rate"] <= ref["skip_rate"] + SKIP_TOLERANCE
        vol_ok = (r["vol_target"] - r["realised_vol"]) <= VOL_SHORTFALL
        ok = dd_ok and skip_ok and vol_ok
        reasons = []
        if not dd_ok: reasons.append(f"DD {r['max_dd']:.2%} > {DD_CAP:.0%}")
        if not skip_ok: reasons.append(
            f"skip {r['skip_rate']:.2%} > ref {ref['skip_rate']:.2%}+{SKIP_TOLERANCE:.0%}")
        if not vol_ok: reasons.append(
            f"realised vol {r['vol_target']-r['realised_vol']:+.2%} short")
        print(f"  {r['vol_target']:>4.0%}  DD {r['max_dd']:>7.2%}  "
              f"skip {r['skip_rate']:>6.2%}  vol short "
              f"{r['vol_target']-r['realised_vol']:>+6.2%}   "
              f"{'QUALIFIES' if ok else 'no: ' + '; '.join(reasons)}")
        if ok:
            qualifying.append(r)

    if not qualifying:
        chosen = None
        verdict = (f"NONE QUALIFIES. No swept vol produces measured drawdown "
                   f"<= {DD_CAP:.0%} without the floor distorting the strategy. "
                   f"Per NOTES 39.1 this means 'B needs vol below 12%', which "
                   f"reopens the floor question rather than resolving it -- a "
                   f"USER decision, not one taken here.")
    else:
        chosen = max(qualifying, key=lambda r: r["vol_target"])
        verdict = (f"DEPLOYMENT VOL = {chosen['vol_target']:.0%} "
                   f"(highest qualifying; measured DD {chosen['max_dd']:.2%}, "
                   f"skip {chosen['skip_rate']:.2%}, realised vol "
                   f"{chosen['realised_vol']:.2%}, Sharpe {chosen['sharpe']:+.3f} "
                   f"-- reported, NOT selected on)")
    print(f"\n  --> {verdict}")

    sh = [r["sharpe"] for r in rows]
    print(f"\n  vol-invariance check: Sharpe ranges {min(sh):+.3f} to {max(sh):+.3f} "
          f"(spread {max(sh)-min(sh):.3f})")
    print(f"  a large spread would mean the floor is interfering non-linearly, "
          f"not that a vol is 'better'")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "vol_sweep_B",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "dd_cap": DD_CAP,
            "skip_tolerance": SKIP_TOLERANCE, "vol_shortfall": VOL_SHORTFALL,
            "rows": rows, "chosen_vol": chosen["vol_target"] if chosen else None,
            "verdict": verdict,
            "note": "risk-sizing diagnostic on train; no trial consumed; 2024 "
                    "not re-run; holdout untouched",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=vol_sweep_B)")


if __name__ == "__main__":
    main()
