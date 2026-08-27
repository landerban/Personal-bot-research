#!/usr/bin/env python3
"""
Stage 3 3: execution-cost sensitivity of the frozen config.

  python tools/cost_curve.py

Same strategy, same data, only the assumed per-side execution cost varies:
0, 2.5, 5, 7.5, 10, 15, 20 bps. That is cost sensitivity on an unchanged
configuration, so it consumes NO trial budget and is logged to
diagnostics.jsonl rather than trials.jsonl.

Headline statistic:

    c* = the maximum per-side execution cost at which annualised net
         return is still > 0

Interpretation is fixed IN ADVANCE (Stage 3 3) so it cannot be chosen after
seeing the number:

    c* > 15 bps      robust to execution quality
    c* in [7, 15]    viable, but execution quality matters and paper-trading
                     cost data becomes critical before going live
    c* < 7 bps       the 5bps assumption rests on ONE synthetic testnet fill,
                     and the strategy may sit inside the noise of its own
                     cost estimate

Also reports the cost at which Sharpe falls below 0.3, the pre-registered
stop threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

POINTS = (0.0, 2.5, 5.0, 7.5, 10.0, 15.0, 20.0)


def interp_zero(xs, ys):
    """Linear crossing of ys through 0 between grid points, or None."""
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), list(zip(xs, ys))[1:]):
        if y0 > 0 >= y1:
            return x0 + (x1 - x0) * y0 / (y0 - y1)
    return None


def interp_level(xs, ys, level):
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), list(zip(xs, ys))[1:]):
        if y0 > level >= y1:
            return x0 + (x1 - x0) * (y0 - level) / (y0 - y1)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)
    rows = []
    print(f"=== Stage 3 3: cost curve | lb{a.lookback}/skip{a.skip} | train | "
          f"FROZEN config, no trials ===")
    print(f"{'bps/side':>9} {'sharpe':>7} {'ann_ret':>9} {'net$':>9} {'fees$':>8} "
          f"{'slip$':>8} {'feedrag':>9} {'maxDD':>7}")
    for bps in POINTS:
        cfg = Config(lookback=a.lookback, skip=a.skip, slippage_bps_per_side=bps)
        res = run_backtest(store, cfg, start, end)
        _, eq = metrics.strategy_window(res)
        rets = metrics.daily_returns(eq)
        r = {
            "bps": bps,
            "sharpe": metrics.sharpe(rets),
            "ann_return": metrics.ann_return(eq),
            "net": float(eq[-1] - cfg.initial_capital),
            "fees": res.total_fees,
            "slippage": res.total_slippage,
            "fee_drag": metrics.fee_drag(res.total_fees, res.gross_pnl),
            "max_dd": metrics.max_drawdown(eq),
        }
        rows.append(r)
        print(f"{bps:>9.1f} {r['sharpe']:>7.2f} {r['ann_return']:>9.2%} "
              f"{r['net']:>+9.2f} {r['fees']:>8.2f} {r['slippage']:>8.2f} "
              f"{r['fee_drag']:>9.2%} {r['max_dd']:>7.1%}")
    store.close()

    xs = [r["bps"] for r in rows]
    c_star = interp_zero(xs, [r["ann_return"] for r in rows])
    s03 = interp_level(xs, [r["sharpe"] for r in rows], 0.3)
    band = ("robust to execution quality; live cost uncertainty is not a threat"
            if c_star and c_star > 15 else
            "viable but execution quality matters; paper-trading cost data is "
            "critical before live" if c_star and c_star >= 7 else
            "the 5bps assumption rests on ONE synthetic testnet fill, and the "
            "strategy may be inside the noise of its own cost estimate")
    print(f"\nc* (net return > 0 up to)   : "
          + (f"{c_star:.1f} bps/side" if c_star else "> 20 bps (never crosses)"))
    print(f"Sharpe < 0.3 (stop) beyond  : "
          + (f"{s03:.1f} bps/side" if s03 else "> 20 bps (never crosses)"))
    print(f"  -> {band}")
    print("\nBaseline is 5 bps (or the eventual measured live cost). The 0-bps "
          "figure is NOT a headline anywhere (Stage 3 3).")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "cost_curve",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "config": asdict(Config(lookback=a.lookback, skip=a.skip)),
            "split": "train", "points": rows, "c_star_bps": c_star,
            "sharpe_below_0_3_bps": s03,
            "note": "cost sensitivity on an unchanged config; no trial consumed",
        }) + "\n")
    print(f"logged to {runner.DIAGNOSTICS_PATH.name} (kind=cost_curve)")


if __name__ == "__main__":
    main()
