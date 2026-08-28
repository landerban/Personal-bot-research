#!/usr/bin/env python3
"""
Stage 3c Part A: block-bootstrap confidence intervals on the rank buckets.

  python tools/bucket_bootstrap.py

Stage 3b reported twelve cells with no error bars, and its headline depended
on a bug whose fix moved 2022 by exactly the amount that decided the branch.
This puts intervals on it. Consumes NO trial budget: it resamples a backtest
that has already run.

METHOD (fixed in NOTES 24 before any interval was computed)
-----------------------------------------------------------
Stationary bootstrap (Politis-Romano 1994): geometric-length blocks with
wraparound, so serial dependence survives resampling.

Resampling is over DAILY series, never over position-days. Positions are
correlated within a day; treating position-days as independent observations
would produce intervals that are too tight.

"Price PnL per position-day" is a RATIO, so each resampled day carries both
its PnL and its position-day count, and the statistic is
sum(pnl) / sum(position_days) over the resample -- not the mean of daily
ratios, which would be a different (and wrong) estimator.

The per-year spread resamples both buckets on the SAME days, preserving
their within-day correlation.
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
LOOKBACK_DAYS = 30
N_BOOT = 2000
CONF = 0.90


def year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def bucket_of(rank: int) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= rank <= hi:
            return name
    raise AssertionError(rank)


def ac1(x: np.ndarray) -> float:
    """Lag-1 autocorrelation."""
    x = x - x.mean()
    d = float(x @ x)
    return float(x[:-1] @ x[1:] / d) if d > 0 else 0.0


def block_indices(n: int, mean_block: float, n_boot: int, rng) -> np.ndarray:
    """Stationary-bootstrap index matrix (n_boot, n)."""
    p = 1.0 / mean_block
    starts = rng.integers(0, n, size=(n_boot, n))
    jumps = rng.random((n_boot, n)) < p
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jumps[:, j], starts[:, j], (idx[:, j - 1] + 1) % n)
    return idx


def ratio_ci(pnl: np.ndarray, days: np.ndarray, mean_block: float, seed: int):
    """90% CI for sum(pnl)/sum(days) under the stationary bootstrap."""
    n = len(pnl)
    if n < 30 or days.sum() <= 0:
        return (float("nan"), float("nan"))
    idx = block_indices(n, mean_block, N_BOOT, np.random.default_rng(seed))
    num = pnl[idx].sum(axis=1)
    den = days[idx].sum(axis=1)
    ok = den > 0
    r = num[ok] / den[ok]
    lo = (1 - CONF) / 2 * 100
    return (float(np.percentile(r, lo)), float(np.percentile(r, 100 - lo)))


def spread_ci(p1, d1, p2, d2, mean_block: float, seed: int):
    """90% CI for ratio(bucket1) - ratio(bucket2), resampling the SAME days."""
    n = len(p1)
    if n < 30 or d1.sum() <= 0 or d2.sum() <= 0:
        return (float("nan"), float("nan"))
    idx = block_indices(n, mean_block, N_BOOT, np.random.default_rng(seed))
    n1, e1 = p1[idx].sum(axis=1), d1[idx].sum(axis=1)
    n2, e2 = p2[idx].sum(axis=1), d2[idx].sum(axis=1)
    ok = (e1 > 0) & (e2 > 0)
    r = n1[ok] / e1[ok] - n2[ok] / e2[ok]
    lo = (1 - CONF) / 2 * 100
    return (float(np.percentile(r, lo)), float(np.percentile(r, 100 - lo)))


def fmt(ci):
    if any(np.isnan(ci)):
        return "        n/a       "
    return f"[{ci[0]:+8.4f},{ci[1]:+8.4f}]"


def excludes_zero(ci) -> bool:
    return not any(np.isnan(ci)) and (ci[0] > 0 or ci[1] < 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, skip=a.skip,
                 slippage_bps_per_side=a.slippage_bps)
    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)
    res = run_backtest(store, cfg, start, end)

    ranks: dict[int, dict[str, int]] = {}
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
            med.append((statistics.median(b.quote_volume for b in bars), sym))
        med.sort(reverse=True)
        ranks[view.as_of] = {sym: i + 1 for i, (_, sym) in enumerate(med)}
    store.close()

    # ---- daily (pnl, position-days) per bucket, same keying as Stage 3b ----
    fills = {rb.ts_fill: rb for rb in res.rebalances}
    sym_decision: dict[str, int] = {}
    daily: dict[str, dict[int, list]] = {b: {} for _, _, b in BUCKETS}
    for i, t in enumerate(res.timestamps):
        rb = fills.get(t)
        if rb is not None:
            for s_ in rb.final_weights:
                sym_decision[s_] = rb.ts_decision
        for b in daily:
            daily[b].setdefault(t, [0.0, 0.0])
        for sym, pnl in res.pnl_by_symbol_day[i].items():
            dts = sym_decision.get(sym)
            r = ranks.get(dts, {}).get(sym) if dts else None
            if r is None:
                continue
            cell = daily[bucket_of(r)][t]
            cell[0] += pnl
            cell[1] += 1.0

    all_ts = sorted(daily["1-30"])
    strat = np.array([sum(daily[b][t][0] for _, _, b in BUCKETS) for t in all_ts])

    # ---- block length, justified from measured autocorrelation ------------
    rho = ac1(strat)
    n_all = len(strat)
    rule = max(2.0, n_all ** (1 / 3))
    # Politis-Romano optimal mean block grows with dependence; for an AR(1)
    # with |rho| this small the n^(1/3) rule already dominates, so take the
    # larger of the two and say so.
    from_rho = 2.0 / max(1e-9, -np.log(abs(rho))) if 0 < abs(rho) < 1 else 0.0
    mean_block = max(rule, from_rho)
    print(f"=== Stage 3c Part A: bucket bootstrap | lb{cfg.lookback}/skip{cfg.skip}"
          f" @ {cfg.slippage_bps_per_side:.0f}bps | FROZEN ===")
    print(f"daily observations {n_all:,} | lag-1 autocorr of daily strategy PnL "
          f"{rho:+.4f}")
    print(f"block length: max(n^(1/3)={rule:.1f}, from rho={from_rho:.1f}) "
          f"= {mean_block:.1f} days, stationary bootstrap, {N_BOOT:,} resamples, "
          f"{CONF:.0%} CI")

    years = sorted({year(t) for t in all_ts})

    def series(b, ys=None):
        ts = [t for t in all_ts if ys is None or year(t) in ys]
        return (np.array([daily[b][t][0] for t in ts]),
                np.array([daily[b][t][1] for t in ts]))

    # ---- 1. twelve cells --------------------------------------------------
    print(f"\n1. price PnL per position-day, per cell, {CONF:.0%} CI")
    print(f"{'year':>5} {'bucket':>8} {'point':>10} {'90% CI':>21} {'excl 0':>7} {'pos-days':>9}")
    cells = {}
    seed = 0
    for y in years:
        for _, _, b in BUCKETS:
            pnl, days = series(b, {y})
            if days.sum() == 0:
                print(f"{y:>5} {b:>8} {'n/a':>10} {'n/a':>21} {'-':>7} {0:>9}"
                      f"   <- universe never reached this rank")
                cells[f"{y}|{b}"] = {"available": False}
                continue
            seed += 1
            pt = pnl.sum() / days.sum()
            ci = ratio_ci(pnl, days, mean_block, seed)
            cells[f"{y}|{b}"] = {"available": True, "point": pt, "ci": ci,
                                 "excludes_zero": excludes_zero(ci),
                                 "pos_days": float(days.sum())}
            print(f"{y:>5} {b:>8} {pt:>+10.4f} {fmt(ci):>21} "
                  f"{'yes' if excludes_zero(ci) else 'no':>7} {days.sum():>9.0f}")

    # ---- 2. per-year spread ----------------------------------------------
    print(f"\n2. top-30 minus 101+ spread, per year  (THE dilution claim)")
    print(f"{'year':>5} {'point':>10} {'90% CI':>21} {'excl 0':>7}")
    spreads = {}
    n_sig = 0
    for y in years:
        p1, d1 = series("1-30", {y})
        p2, d2 = series("101+", {y})
        if d2.sum() == 0:
            print(f"{y:>5} {'n/a':>10} {'n/a':>21} {'-':>7}   <- 101+ unavailable")
            spreads[str(y)] = {"available": False}
            continue
        seed += 1
        pt = p1.sum() / d1.sum() - p2.sum() / d2.sum()
        ci = spread_ci(p1, d1, p2, d2, mean_block, seed)
        ok = excludes_zero(ci)
        n_sig += bool(ok)
        spreads[str(y)] = {"available": True, "point": pt, "ci": ci, "excludes_zero": ok}
        print(f"{y:>5} {pt:>+10.4f} {fmt(ci):>21} {'yes' if ok else 'no':>7}")

    # ---- 3 & 4. pooled ----------------------------------------------------
    print(f"\n3/4. pooled")
    pooled = {}
    for label, b, ys in (("top-30, all 4 years", "1-30", None),
                         ("101+, the 3 years it exists", "101+", {2021, 2022, 2023})):
        pnl, days = series(b, ys)
        seed += 1
        pt = pnl.sum() / days.sum()
        ci = ratio_ci(pnl, days, mean_block, seed)
        pooled[b] = {"point": pt, "ci": ci, "excludes_zero": excludes_zero(ci),
                     "below_zero": (not any(np.isnan(ci))) and ci[1] < 0}
        print(f"  {label:<30} {pt:>+9.4f}  {fmt(ci)}  "
              f"{'excludes 0' if excludes_zero(ci) else 'straddles 0'}")

    # ---- non-gating: 2021 top-30, and 31-100 vs 1-30 ----------------------
    print(f"\nnon-gating checks (NOTES 24)")
    c2021 = cells.get("2021|1-30", {})
    print(f"  2021 top-30 distinguishable from zero? "
          f"{'YES' if c2021.get('excludes_zero') else 'NO'}  "
          f"(point {c2021.get('point', float('nan')):+.4f}, CI {fmt(c2021.get('ci', (np.nan, np.nan)))})")
    print(f"  does 31-100 beating 1-30 survive its own CI?")
    mid_ci = {}
    for y in (2020, 2021, 2022, 2023):
        p1, d1 = series("31-100", {y})
        p2, d2 = series("1-30", {y})
        if d1.sum() == 0 or d2.sum() == 0:
            continue
        seed += 1
        pt = p1.sum() / d1.sum() - p2.sum() / d2.sum()
        ci = spread_ci(p1, d1, p2, d2, mean_block, seed)
        mid_ci[str(y)] = {"point": pt, "ci": ci, "excludes_zero": excludes_zero(ci)}
        print(f"    {y}  (31-100) - (1-30) = {pt:>+8.4f}  {fmt(ci)}  "
              f"{'significant' if excludes_zero(ci) else 'not significant'}")

    # ---- branch ----------------------------------------------------------
    print(f"\n=== BRANCH (NOTES 24, fixed in advance) ===")
    pooled_below = pooled["101+"]["below_zero"]
    only_2023 = (n_sig == 1 and spreads.get("2023", {}).get("excludes_zero"))
    print(f"  pooled 101+ CI entirely below zero : {pooled_below}")
    print(f"  spread excludes zero in {n_sig} of 3 years")
    if pooled_below and n_sig >= 2:
        branch = "ONE: dilution survives -> proceed to Part B"
    elif pooled_below and only_2023:
        branch = "THREE: weak, rests on 2023 alone -> STOP and report; decision to the user"
    else:
        branch = "TWO: dilution not established -> STOP, do not run Part B"
    print(f"  --> BRANCH {branch}")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "bucket_bootstrap",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "config_hash": runner.config_hash(cfg), "split": "train",
            "mean_block_days": mean_block, "lag1_autocorr": rho,
            "n_boot": N_BOOT, "confidence": CONF,
            "cells": cells, "spreads": spreads, "pooled": pooled,
            "mid_vs_top": mid_ci, "n_years_spread_significant": n_sig,
            "branch": branch,
            "note": "resampling of a frozen, already-run config; no trial",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=bucket_bootstrap)")


if __name__ == "__main__":
    main()
