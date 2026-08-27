#!/usr/bin/env python3
"""
Stage 3 2: per-calendar-year attribution of the frozen config.

  python tools/per_year.py [--lookback 14] [--skip 0] [--slippage-bps 5]

Slicing a backtest that has already run is attribution, not selection, so
this consumes NO trial budget. The config is FROZEN (Stage 3 4) -- nothing
here may be used to pick a different one.

The decisive question (Stage 3 2.1): documented crypto carry ran Sharpe
6.45 over 2020-2025, 4.06 from 2024, and turned NEGATIVE in 2025 -- inside
the holdout window. So: does funding PnL hold across all four train years,
or is it concentrated in 2020-21 and decaying?

Writes diagnostics.jsonl, never trials.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402


def year_of(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def slice_year(res, year: int) -> dict:
    """Everything attributable to one calendar year of a completed run."""
    idx = [i for i, t in enumerate(res.timestamps) if year_of(t) == year]
    if len(idx) < 2:
        return {}
    lo, hi = idx[0], idx[-1]
    eq = np.array(res.equity[max(lo - 1, 0):hi + 1])
    rets = metrics.daily_returns(eq)

    price = long_pnl = short_pnl = 0.0
    for i in idx:
        for sym, v in res.pnl_by_symbol_day[i].items():
            price += v
    # side attribution needs the sign of the position that earned it, which
    # the daily trace does not carry; recover it from the ruling rebalance
    fills_by_ts = {rb.ts_fill: rb for rb in res.rebalances}
    held_sign: dict[str, float] = {}
    for i, t in enumerate(res.timestamps):
        rb = fills_by_ts.get(t)
        if rb is not None:
            held_sign = {s_: (1.0 if w > 0 else -1.0)
                         for s_, w in rb.final_weights.items()}
        if year_of(t) != year:
            continue
        for sym, v in res.pnl_by_symbol_day[i].items():
            if held_sign.get(sym, 0.0) > 0:
                long_pnl += v
            elif held_sign.get(sym, 0.0) < 0:
                short_pnl += v

    fees = sum(rb.fees for rb in res.rebalances if year_of(rb.ts_fill) == year)
    fees += sum(rc.fees for rc in res.rescales if year_of(rc.ts_fill) == year)
    turn = sum(rb.turnover_notional for rb in res.rebalances
               if year_of(rb.ts_fill) == year)
    turn += sum(rc.turnover_notional for rc in res.rescales
                if year_of(rc.ts_fill) == year)
    lev = [rb.realised_gross_leverage for rb in res.rebalances
           if year_of(rb.ts_fill) == year]
    # funding = the residual of the equity change once price and fees are known
    net = float(eq[-1] - eq[0])
    funding = net - price + fees
    return {
        "days": len(idx),
        "sharpe": metrics.sharpe(rets),
        "ann_vol": metrics.ann_vol(rets),
        "max_dd": metrics.max_drawdown(eq),
        "net": net,
        "price_pnl": price,
        "funding_pnl": funding,
        "fees": fees,
        "turnover": turn,
        "long_pnl": long_pnl,
        "short_pnl": short_pnl,
        "lev_median": float(np.median(lev)) if lev else float("nan"),
        "equity_start": float(eq[0]),
        "equity_end": float(eq[-1]),
        "n_rebalances": len(lev),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    ap.add_argument("--years", default="2020,2021,2022,2023")
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, skip=a.skip,
                 slippage_bps_per_side=a.slippage_bps)
    years = [int(y) for y in a.years.split(",")]
    start, end = runner.split_view_range("train")

    store = PointInTimeStore(a.db, read_only=True)
    res = run_backtest(store, cfg, start, end)
    store.close()

    print(f"=== Stage 3 2: per-year attribution | lb{cfg.lookback}/skip{cfg.skip} "
          f"@ {cfg.slippage_bps_per_side:.1f}bps | FROZEN config, diagnosis only ===")
    hdr = (f"{'year':>5} {'days':>5} {'sharpe':>7} {'net$':>9} {'price$':>9} "
           f"{'funding$':>9} {'fees$':>8} {'long$':>9} {'short$':>9} "
           f"{'vol':>6} {'maxDD':>6} {'lev':>5} {'turn$':>10}")
    print(hdr)
    out = {}
    for y in years:
        r = slice_year(res, y)
        if not r:
            continue
        out[y] = r
        print(f"{y:>5} {r['days']:>5} {r['sharpe']:>7.2f} {r['net']:>+9.2f} "
              f"{r['price_pnl']:>+9.2f} {r['funding_pnl']:>+9.2f} {r['fees']:>8.2f} "
              f"{r['long_pnl']:>+9.2f} {r['short_pnl']:>+9.2f} "
              f"{r['ann_vol']:>6.1%} {r['max_dd']:>6.1%} {r['lev_median']:>5.2f} "
              f"{r['turnover']:>10.0f}")

    fs = [out[y]["funding_pnl"] for y in out]
    ps = [out[y]["price_pnl"] for y in out]
    print(f"\nfunding PnL by year: {' -> '.join(f'{v:+.0f}' for v in fs)}")
    print(f"price   PnL by year: {' -> '.join(f'{v:+.0f}' for v in ps)}")
    early = sum(v for y, v in zip(out, fs) if y <= 2021)
    late = sum(v for y, v in zip(out, fs) if y >= 2022)
    tot = sum(fs)
    if tot:
        print(f"funding 2020-21 vs 2022-23: {early:+.0f} vs {late:+.0f} "
              f"({early / tot:.0%} / {late / tot:.0%} of total)")
    print("\nStage 3 2.1 reading: funding roughly flat across years -> mechanism "
          "durable, validate worth a trial. Concentrated in 2020-21 and decaying "
          "-> the aggregate is historical and the holdout sits in the decayed regime.")

    rec = {"ts": int(time.time()), "kind": "per_year_attribution",
           "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
           "config_hash": runner.config_hash(cfg), "config": asdict(cfg),
           "split": "train", "years": {str(k): v for k, v in out.items()},
           "note": "attribution of a frozen, already-run config; no trial consumed"}
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=per_year_attribution)")


if __name__ == "__main__":
    main()
