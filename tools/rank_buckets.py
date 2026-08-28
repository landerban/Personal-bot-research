#!/usr/bin/env python3
"""
Stage 3b: rank-bucket attribution of the frozen config's train run.

  python tools/rank_buckets.py

Tests the COMPOSITION axis that Stage 3a's dispersion work left open: did
the price-PnL decline (+163 -> +110 -> +30 -> -37) come from alpha decay, or
from the universe growing (29 -> 166) into a segment where the published
momentum payoff is negative?

Consumes NO trial budget: no configuration changes, no signal is altered,
nothing new is evaluated. The reading was fixed in NOTES 23 and committed
before this ran.

METHOD
------
Liquidity rank is computed POINT-IN-TIME through PITView at each rebalance
date, using the same measure `universe()` uses -- median daily quote volume
over the trailing 30 bars, 1 = most liquid. A rank derived from present-day
liquidity would be lookahead, and the habit matters more than this one
statistic; every bar consumed is asserted to have close_time <= as_of.

The per-symbol daily PnL trace is the input (it reconciles to gross_pnl
exactly). The engine is run once only to materialise that trace, which is
in-memory and not persisted -- no PnL is recomputed by any other route.

Bucket price PnL is reconciled against per-year totals; a mismatch aborts,
because the trace is the input to everything here.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

DAY = 86_400_000
BUCKETS = ((1, 30, "1-30"), (31, 100, "31-100"), (101, 10**9, "101+"))
LOOKBACK_DAYS = 30          # matches universe()


def year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def bucket_of(rank: int) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= rank <= hi:
            return name
    raise AssertionError(rank)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--max-liquidity-rank", type=int, default=None)
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, skip=a.skip,
                 slippage_bps_per_side=a.slippage_bps,
                 max_liquidity_rank=a.max_liquidity_rank)
    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)
    res = run_backtest(store, cfg, start, end)

    # ---- point-in-time liquidity ranks at each decision date -------------
    # One pass over the same view sequence the run used.
    ranks: dict[int, dict[str, int]] = {}
    uni_size: dict[int, int] = {}
    n_bars_checked = 0
    store.reset_clock()
    for view in store.iter_views(start, end, DAY):
        uni = view.universe(cfg.min_quote_volume)
        if not uni:
            continue
        med = []
        for sym in uni:
            bars = view.klines(sym, "1d", limit=LOOKBACK_DAYS)
            if len(bars) < LOOKBACK_DAYS:
                continue
            for b in bars:                       # point-in-time assertion
                assert b.close_time <= view.as_of, (
                    f"lookahead: {sym} bar close_time {b.close_time} > "
                    f"as_of {view.as_of}")
            n_bars_checked += len(bars)
            med.append((statistics.median(b.quote_volume for b in bars), sym))
        med.sort(reverse=True)                   # most liquid first
        ranks[view.as_of] = {sym: i + 1 for i, (_, sym) in enumerate(med)}
        uni_size[view.as_of] = len(med)
    store.close()
    print(f"ranks computed at {len(ranks):,} dates; {n_bars_checked:,} bars "
          f"asserted point-in-time (close_time <= as_of)")

    # ---- attribute each held position-day to a bucket --------------------
    # The book held on day D was decided at the previous rebalance; use the
    # rank as of that decision date, which is what the strategy could see.
    fills_by_ts = {rb.ts_fill: rb for rb in res.rebalances}
    held_sign: dict[str, float] = {}
    # Rank each symbol under the decision that actually HELD it. Keying off
    # the current book mis-assigns exit-day PnL: a symbol dropped at this
    # rebalance still books PnL today (it is marked before the fill), and
    # the new decision does not contain it -- which left LUNAUSDT's +24.69
    # exit unranked on 2022-02-28 and stranded $30.82, more than 2022's
    # entire price PnL, outside the buckets.
    sym_decision: dict[str, int] = {}
    agg = defaultdict(lambda: defaultdict(float))   # (year,bucket) -> fields
    unranked = defaultdict(float)
    price_by_year = defaultdict(float)

    funding_by_day = defaultdict(lambda: defaultdict(float))
    for t, sym, units, rate, amt in res.funding_rows:
        funding_by_day[t][sym] += amt

    for i, t in enumerate(res.timestamps):
        rb = fills_by_ts.get(t)
        if rb is not None:
            held_sign = {s_: (1.0 if w > 0 else -1.0)
                         for s_, w in rb.final_weights.items()}
            # symbols in the new book re-key to this decision; symbols
            # leaving it keep the decision under which they were held
            for s_ in rb.final_weights:
                sym_decision[s_] = rb.ts_decision
        y = year(t)
        day_trace = res.pnl_by_symbol_day[i]
        day_fund = funding_by_day.get(t, {})
        for sym in set(day_trace) | set(day_fund):
            pnl = day_trace.get(sym, 0.0)
            fund = day_fund.get(sym, 0.0)
            price_by_year[y] += pnl
            dts = sym_decision.get(sym)
            r = ranks.get(dts, {}).get(sym) if dts else None
            if r is None:
                unranked[y] += pnl
                continue
            b = bucket_of(r)
            k = agg[(y, b)]
            k["price"] += pnl
            k["funding"] += fund
            k["days"] += 1.0
            side = "long" if held_sign.get(sym, 0.0) > 0 else "short"
            k[f"price_{side}"] += pnl
            k["rank_sum"] += r

    # ---- reconciliation: bucket sums must equal per-year price PnL -------
    print("\nreconciliation (bucket price PnL vs per-year total):")
    ok = True
    for y in sorted(price_by_year):
        bsum = sum(agg[(y, b)]["price"] for _, _, b in BUCKETS) + unranked[y]
        diff = bsum - price_by_year[y]
        flag = "OK" if abs(diff) < 1e-6 else "MISMATCH"
        if abs(diff) >= 1e-6:
            ok = False
        print(f"  {y}  buckets {bsum:>+10.4f}  total {price_by_year[y]:>+10.4f}  "
              f"diff {diff:>+.2e}  {flag}"
              + (f"   (unranked {unranked[y]:+.2f})" if abs(unranked[y]) > 1e-9 else ""))
    if not ok:
        sys.exit("STOP: bucket price PnL does not reconcile to per-year totals; "
                 "the trace is the input to everything here (STAGE3B 6.3)")

    # ---- report ---------------------------------------------------------
    years = sorted(price_by_year)
    print(f"\n=== Stage 3b: rank-bucket attribution | lb{cfg.lookback}/skip{cfg.skip}"
          f" @ {cfg.slippage_bps_per_side:.0f}bps | FROZEN ===")
    print(f"{'year':>5} {'bucket':>8} {'avail':>6} {'price$':>9} {'funding$':>9} "
          f"{'pos-days':>9} {'share':>7} {'price/day':>10} {'long$':>9} {'short$':>9}")
    out = {}
    for y in years:
        tot_days = sum(agg[(y, b)]["days"] for _, _, b in BUCKETS)
        umax = max((uni_size[t] for t in uni_size if year(t) == y), default=0)
        for lo, _, b in BUCKETS:
            k = agg[(y, b)]
            avail = "yes" if umax >= lo else "n/a"
            if k["days"] == 0:
                print(f"{y:>5} {b:>8} {avail:>6} {'-':>9} {'-':>9} {0:>9} "
                      f"{'-':>7} {'-':>10} {'-':>9} {'-':>9}"
                      + ("   <- universe never reached this rank" if avail == "n/a" else ""))
                out[f"{y}|{b}"] = {"available": avail == "yes", "days": 0}
                continue
            share = k["days"] / tot_days if tot_days else float("nan")
            ppd = k["price"] / k["days"]
            print(f"{y:>5} {b:>8} {avail:>6} {k['price']:>+9.2f} {k['funding']:>+9.2f} "
                  f"{k['days']:>9.0f} {share:>7.1%} {ppd:>+10.4f} "
                  f"{k['price_long']:>+9.2f} {k['price_short']:>+9.2f}")
            out[f"{y}|{b}"] = {"available": True, "price": k["price"],
                               "funding": k["funding"], "days": k["days"],
                               "share": share, "price_per_day": ppd,
                               "long": k["price_long"], "short": k["price_short"]}
        print()

    print("=== THE TEST: top-30 price PnL per position-day, by year ===")
    series = []
    for y in years:
        k = agg[(y, "1-30")]
        v = k["price"] / k["days"] if k["days"] else float("nan")
        series.append(v)
        print(f"  {y}  {v:>+9.4f} $/position-day   ({k['days']:>5.0f} position-days)")
    print(f"  series: {' -> '.join(f'{v:+.4f}' for v in series)}")

    print("\n=== 101+ share of position-days (the dilution mechanism) ===")
    for y in years:
        tot_days = sum(agg[(y, b)]["days"] for _, _, b in BUCKETS)
        k = agg[(y, "101+")]
        print(f"  {y}  {k['days'] / tot_days if tot_days else 0:>6.1%}  "
              f"({k['days']:>5.0f} of {tot_days:>5.0f} position-days)")

    print("\nReading (NOTES 23, fixed in advance):")
    print("  top-30 positive all 4 years AND 101+ negative with rising share -> DILUTION")
    print("  top-30 declines like the aggregate                              -> DECAY")
    print("  no consistent rank pattern                                      -> NEITHER")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "rank_buckets",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "config_hash": runner.config_hash(cfg), "split": "train",
            "buckets": [b for _, _, b in BUCKETS], "cells": out,
            "top30_price_per_day": {str(y): v for y, v in zip(years, series)},
            "note": "attribution of a frozen, already-run config; no trial",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=rank_buckets)")


if __name__ == "__main__":
    main()
