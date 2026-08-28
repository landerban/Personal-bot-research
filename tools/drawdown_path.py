#!/usr/bin/env python3
"""
Stage 5a: B1's un-truncated drawdown path on train. Diagnostic, zero trials.

  python tools/drawdown_path.py

Answers: B1's max drawdown is 29.73% against a 30% kill switch -- if the
strategy had NOT been stopped there, would it have recovered or kept falling?

NO kill-switch-disabled flag exists or is needed. The 30% switch has never
been implemented in the engine; it is a pre-registered OPERATIONAL criterion
(NOTES 30 Tier 1). The engine's only early exit is bankruptcy, which B1 never
approaches, so its recorded equity path is already the un-truncated one and
this is pure analysis of an existing series.

Not logged as a trial: a re-run of an existing config with reporting
un-truncated is not a new configuration.
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

SWITCH = 0.30
EXCURSION = 0.25


def d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                 max_liquidity_rank=15)          # B1
    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)
    res = run_backtest(store, cfg, start, end)
    store.close()
    assert not res.bankrupt, "engine truncated on bankruptcy; path is not complete"

    # strategy window, the basis every other figure uses
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    ts = res.timestamps[lo:]
    eq = np.array(res.equity[lo:])
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks

    print(f"=== Stage 5a: B1 un-truncated drawdown path | train | no trial ===")
    print(f"{len(ts):,} days {d(ts[0])} -> {d(ts[-1])}   "
          f"equity ${eq[0]:.2f} -> ${eq[-1]:.2f}   max drawdown {dd.max():.2%}")

    # ---- the deepest drawdown: peak, trough, recovery --------------------
    j = int(np.argmax(dd))
    pk = int(np.argmax(eq[:j + 1]))
    rec = next((k for k in range(j, len(eq)) if eq[k] >= eq[pk]), None)
    print(f"\ndeepest drawdown {dd[j]:.2%}")
    print(f"  peak    {d(ts[pk])}  ${eq[pk]:.2f}")
    print(f"  trough  {d(ts[j])}  ${eq[j]:.2f}   ({j - pk} days from peak)")
    if rec is None:
        print(f"  recovery: DID NOT RECOVER within train "
              f"({len(eq) - 1 - j} days after the trough, still "
              f"{dd[-1]:.2%} below the peak at the end)")
        underwater = len(eq) - 1 - pk
    else:
        print(f"  recovery {d(ts[rec])}  ${eq[rec]:.2f}   "
              f"({rec - j} days from trough)")
        underwater = rec - pk
    print(f"  time underwater: {underwater} days "
          f"({underwater/30.44:.1f} months) from peak to "
          f"{'recovery' if rec else 'end of train'}")

    # ---- every excursion past -25% ---------------------------------------
    print(f"\nexcursions past {EXCURSION:.0%}:")
    runs, k = [], 0
    while k < len(dd):
        if dd[k] > EXCURSION:
            s0 = k
            while k < len(dd) and dd[k] > EXCURSION:
                k += 1
            seg = slice(s0, k)
            deep = int(np.argmax(dd[seg])) + s0
            p0 = int(np.argmax(eq[:deep + 1]))
            r0 = next((m for m in range(deep, len(eq)) if eq[m] >= eq[p0]), None)
            runs.append((s0, k - 1, deep, p0, r0))
        else:
            k += 1
    if not runs:
        print("  none")
    for s0, e0, deep, p0, r0 in runs:
        print(f"  {d(ts[s0])} -> {d(ts[e0])}  deepest {dd[deep]:.2%} on {d(ts[deep])}"
              f" | recovery "
              + (f"{d(ts[r0])} ({r0 - deep} days from trough)" if r0
                 else "NOT within train"))

    # ---- the -30% crossing and what happened after -----------------------
    crossed = np.flatnonzero(dd >= SWITCH)
    print(f"\nthe {SWITCH:.0%} kill switch:")
    if len(crossed) == 0:
        print(f"  never reached -- worst was {dd.max():.2%} on {d(ts[j])}, "
              f"{(SWITCH - dd.max())*100:.2f} points of headroom")
        print(f"  so there is no 'after the switch fired' path to inspect: the "
              f"strategy was never stopped on train.")
        print(f"\n  what happened after the WORST point ({d(ts[j])}), for reference:")
        for h in (30, 60, 90):
            k2 = min(j + h, len(eq) - 1)
            print(f"    +{h:>3}d  {d(ts[k2])}  equity ${eq[k2]:>7.2f}  "
                  f"({eq[k2]/eq[j]-1:+.2%} from the trough, "
                  f"drawdown {dd[k2]:.2%})")
    else:
        f0 = int(crossed[0])
        print(f"  would have fired {d(ts[f0])} at {dd[f0]:.2%}")
        for h in (30, 60, 90):
            k2 = min(f0 + h, len(eq) - 1)
            print(f"    +{h:>3}d  {d(ts[k2])}  equity ${eq[k2]:>7.2f}  "
                  f"({eq[k2]/eq[f0]-1:+.2%} from the switch point, "
                  f"drawdown {dd[k2]:.2%})")

    # ---- monthly equity curve -------------------------------------------
    print("\nmonthly equity (close):")
    seen = {}
    for t, e in zip(ts, eq):
        seen[d(t)[:7]] = float(e)
    months = sorted(seen)
    for m0 in range(0, len(months), 6):
        print("  " + "  ".join(f"{m} {seen[m]:>7.0f}" for m in months[m0:m0 + 6]))

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "drawdown_path_B1",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "config": "B1 top-15 majors",
            "max_dd": float(dd.max()), "peak": d(ts[pk]), "trough": d(ts[j]),
            "recovery": d(ts[rec]) if rec else None,
            "days_underwater": int(underwater),
            "n_excursions_past_25pct": len(runs),
            "switch_ever_reached": bool(len(crossed)),
            "monthly_equity": seen,
            "note": "diagnostic on an existing config; no trial; the 30% switch "
                    "is not implemented in the engine so the path was never "
                    "truncated and no disabling flag exists",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=drawdown_path_B1)")


if __name__ == "__main__":
    main()
