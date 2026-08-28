#!/usr/bin/env python3
"""
Stage 3a 3, 4, 5: drawdown timing, funding by regime, crash resilience.

  python tools/regime.py

Attribution of the frozen config's already-run backtest. Evaluates no new
strategy and selects nothing, so it consumes NO trial budget.

3  Max drawdown per calendar year with peak/trough dates, the global trough,
   a monthly equity curve, and the question that matters most: WOULD THE 30%
   KILL SWITCH EVER HAVE FIRED on the daily path? An annual Sharpe says
   nothing about the intra-year path, and if the drawdown breached 30%
   intra-year then under our own pre-registered rules the strategy would
   have been shut down mid-run -- meaning the four-year Sharpe describes a
   path we would not have completed.
4  Monthly funding, funding by leg, funding bucketed by trailing BTC
   drawdown depth, and the fraction of days the SHORT leg paid rather than
   received. In a crash shorts become the crowded side, so the largest
   income line inverts exactly when the price leg is starved.
5  2022 vs other years: leverage, realised beta (did the hedge hold under a
   correlation spike?), skips by reason, and closest approach to
   liquidation from the intraday H/L stress path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from backtest.weights import BTC  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

KILL_SWITCH = 0.30


def d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def ym(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def dd_path(ts, eq):
    """(running drawdown, peak index) for an equity path."""
    peaks = np.maximum.accumulate(eq)
    return (peaks - eq) / peaks, peaks


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

    ts, eq = metrics.strategy_window(res)
    ts = list(ts)
    dd, peaks = dd_path(ts, eq)
    out = {}

    # ---------------------------------------------------------------- 3
    print(f"=== Stage 3a 3: drawdown timing | lb{cfg.lookback}/skip{cfg.skip} "
          f"@ {cfg.slippage_bps_per_side:.0f}bps | FROZEN ===")
    gi = int(np.argmax(dd))
    gpeak = int(np.argmax(eq[:gi + 1])) if gi else 0
    print(f"global max drawdown {dd[gi]:.2%}  peak {d(ts[gpeak])} (${eq[gpeak]:.2f}) "
          f"-> trough {d(ts[gi])} (${eq[gi]:.2f})")
    print(f"\n{'year':>5} {'maxDD':>7} {'peak':>12} {'trough':>12} "
          f"{'eq peak':>9} {'eq trough':>10}")
    yearly = {}
    for y in sorted({year(t) for t in ts}):
        idx = [i for i, t in enumerate(ts) if year(t) == y]
        if len(idx) < 2:
            continue
        sub = eq[idx[0]:idx[-1] + 1]
        sdd, _ = dd_path(None, sub)
        j = int(np.argmax(sdd))
        pk = int(np.argmax(sub[:j + 1])) if j else 0
        yearly[y] = {"max_dd": float(sdd[j]),
                     "peak_date": d(ts[idx[0] + pk]), "trough_date": d(ts[idx[0] + j]),
                     "eq_peak": float(sub[pk]), "eq_trough": float(sub[j])}
        v = yearly[y]
        print(f"{y:>5} {v['max_dd']:>7.2%} {v['peak_date']:>12} {v['trough_date']:>12} "
              f"{v['eq_peak']:>9.2f} {v['eq_trough']:>10.2f}")

    fired = dd >= KILL_SWITCH
    print(f"\n30% KILL SWITCH on the daily path: "
          f"{'FIRED' if fired.any() else 'never fired'}", end="")
    if fired.any():
        k = int(np.argmax(fired))
        print(f" -- first breach {d(ts[k])} at {dd[k]:.2%}, "
              f"{int(fired.sum())} of {len(dd)} days at or beyond 30%")
    else:
        print(f" (worst {dd.max():.2%})")

    print("\nmonthly equity (close):")
    seen = {}
    for t, e in zip(ts, eq):
        seen[ym(t)] = float(e)
    months = sorted(seen)
    for i in range(0, len(months), 6):
        print("  " + "  ".join(f"{m} {seen[m]:>7.0f}" for m in months[i:i + 6]))

    # ---------------------------------------------------------------- 4
    print(f"\n=== Stage 3a 4: funding by regime ===")
    fm = defaultdict(float)
    fleg = defaultdict(float)
    short_day = defaultdict(float)
    for t, sym, units, rate, amt in res.funding_rows:
        fm[ym(t)] += amt
        fleg["long" if units > 0 else "short"] += amt
        if units < 0:
            short_day[t] += amt
    print(f"funding by leg: long {fleg['long']:+.2f} | short {fleg['short']:+.2f} "
          f"| total {sum(fleg.values()):+.2f}")
    paid = sum(1 for v in short_day.values() if v < 0)
    recv = sum(1 for v in short_day.values() if v > 0)
    print(f"short leg: PAID on {paid} days, RECEIVED on {recv} days "
          f"({paid / max(paid + recv, 1):.1%} of days paid)")
    print("\nmonthly funding PnL:")
    for i in range(0, len(months), 6):
        print("  " + "  ".join(f"{m} {fm.get(m, 0.0):>+7.1f}" for m in months[i:i + 6]))

    # bucket by trailing BTC drawdown depth
    v_end = store.view_as_of(end)
    bars = v_end.klines(BTC, "1d", limit=5000)
    bclose = {b.close_time: b.close for b in bars}
    bts = sorted(bclose)
    bvals = np.array([bclose[t] for t in bts])
    bpeak = np.maximum.accumulate(bvals)
    bdd = {t: float((p - c) / p) for t, c, p in zip(bts, bvals, bpeak)}
    buckets = defaultdict(float)
    bcount = defaultdict(int)
    for t, sym, units, rate, amt in res.funding_rows:
        depth = bdd.get(t)
        if depth is None:
            continue
        b = ("0-10%" if depth < 0.10 else "10-30%" if depth < 0.30
             else "30-50%" if depth < 0.50 else ">50%")
        buckets[b] += amt
        bcount[b] += 1
    print("\nfunding bucketed by trailing BTC drawdown depth:")
    for b in ("0-10%", "10-30%", "30-50%", ">50%"):
        if bcount[b]:
            print(f"  BTC dd {b:<7} funding {buckets[b]:>+9.2f}  "
                  f"({bcount[b]:,} settlements)")

    # ---------------------------------------------------------------- 5
    print(f"\n=== Stage 3a 5: crash resilience ===")
    print(f"{'year':>5} {'lev med':>8} {'lev p95':>8} {'realised beta':>14} "
          f"{'rebal':>6} {'skips':>6}")
    for y in sorted(yearly):
        lev = [rb.realised_gross_leverage for rb in res.rebalances
               if year(rb.ts_fill) == y]
        idx = [i for i, t in enumerate(ts) if year(t) == y]
        r = metrics.daily_returns(eq[idx[0]:idx[-1] + 1])
        bt = []
        for t in ts[idx[0]:idx[-1] + 1]:
            bt.append(bclose.get(t, np.nan))
        bt = np.array(bt, dtype=float)
        br = np.diff(bt) / bt[:-1]
        n = min(len(r), len(br))
        ok = np.isfinite(r[:n]) & np.isfinite(br[:n])
        beta = (float(np.cov(r[:n][ok], br[:n][ok])[0, 1] / np.var(br[:n][ok]))
                if ok.sum() > 10 and np.var(br[:n][ok]) > 0 else float("nan"))
        nsk = sum(1 for t2, _, _ in res.skips if year(t2) == y)
        yearly[y].update(lev_median=float(np.median(lev)) if lev else float("nan"),
                         lev_p95=float(np.percentile(lev, 95)) if lev else float("nan"),
                         realised_beta=beta, n_rebalances=len(lev), n_skips=nsk)
        v = yearly[y]
        print(f"{y:>5} {v['lev_median']:>8.2f} {v['lev_p95']:>8.2f} "
              f"{v['realised_beta']:>+14.3f} {v['n_rebalances']:>6} {v['n_skips']:>6}")

    print("\n2022 skips by reason:")
    r2022 = defaultdict(int)
    for t2, reason, _ in res.skips:
        if year(t2) == 2022:
            r2022[reason] += 1
    for k, v in sorted(r2022.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<32} {v}")

    worst = {t: e for t, e in res.daily_worst_equity}
    ratios = [(worst[t] / e, t) for t, e in zip(ts, eq) if t in worst and e > 0]
    mn, mnt = min(ratios)
    print(f"\nclosest approach to liquidation (intraday H/L stress):")
    print(f"  min worst-case equity / close equity = {mn:.3f} on {d(mnt)}")
    gmin = min((e for _, e in res.daily_worst_equity))
    print(f"  min implied equity ${gmin:.2f} = {gmin / cfg.initial_capital:.0%} "
          f"of starting capital")
    store.close()

    out = {"yearly": {str(k): v for k, v in yearly.items()},
           "kill_switch_fired": bool(fired.any()),
           "global_max_dd": float(dd.max()),
           "global_trough_date": d(ts[gi]),
           "funding_by_leg": dict(fleg),
           "funding_by_btc_dd": dict(buckets),
           "short_paid_days": paid, "short_received_days": recv,
           "monthly_funding": dict(fm), "monthly_equity": seen,
           "min_stress_ratio": mn, "min_stress_date": d(mnt)}
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "regime_diagnostics",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "config_hash": runner.config_hash(cfg), "split": "train", **out,
            "note": "attribution of a frozen, already-run config; no trial",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=regime_diagnostics)")


if __name__ == "__main__":
    main()
