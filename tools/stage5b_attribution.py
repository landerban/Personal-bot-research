#!/usr/bin/env python3
"""
Stage 5b: free attribution on B (top-15 PIT majors). Zero trials.

  python tools/stage5b_attribution.py

1  per-year block-bootstrap 90% CIs on B's price PnL, funding PnL and Sharpe
2  drift decomposition on B -- the one that can overturn Stage 5
3  turnover: annualised multiple and boundary-crossing vs adjustment, B vs A
4  inputs for the payer story at B's 77/23 composition

Everything resamples or slices runs that already exist. The demeaned run is
DIAGNOSTIC ONLY (full-sample means, not runnable live) and is logged to
diagnostics.jsonl, never trials.jsonl.
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
from pitdata.store import PointInTimeStore  # noqa: E402

N_BOOT, CONF = 2000, 0.90


def year(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year
def ac1(x):
    x = x - x.mean(); dd = float(x @ x)
    return float(x[:-1] @ x[1:] / dd) if dd > 0 else 0.0


def blocks(n, mb, seed):
    rng = np.random.default_rng(seed); p = 1.0 / mb
    st = rng.integers(0, n, size=(N_BOOT, n)); jp = rng.random((N_BOOT, n)) < p
    idx = np.empty((N_BOOT, n), dtype=np.int64); idx[:, 0] = st[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jp[:, j], st[:, j], (idx[:, j - 1] + 1) % n)
    return idx


def ci(v):
    v = v[np.isfinite(v)]
    if len(v) < N_BOOT // 10: return (float("nan"), float("nan"))
    lo = (1 - CONF) / 2 * 100
    return (float(np.percentile(v, lo)), float(np.percentile(v, 100 - lo)))


def fmt(c): return "n/a" if any(np.isnan(c)) else f"[{c[0]:+8.2f},{c[1]:+8.2f}]"
def fmt2(c): return "n/a" if any(np.isnan(c)) else f"[{c[0]:+6.2f},{c[1]:+6.2f}]"
def excl(c): return not any(np.isnan(c)) and (c[0] > 0 or c[1] < 0)


def window(res):
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return res.timestamps[lo:], np.array(res.equity[lo:])


def daily_parts(res):
    """(ts, price_t, funding_t, fee_t) aligned day by day."""
    fund = defaultdict(float)
    for t, _, _, _, amt in res.funding_rows:
        fund[t] += amt
    fees = {t: f for t, f, _ in res.fees_by_day}
    ts, price, fu, fe = [], [], [], []
    for i, t in enumerate(res.timestamps):
        ts.append(t)
        price.append(sum(res.pnl_by_symbol_day[i].values()))
        fu.append(fund.get(t, 0.0))
        fe.append(fees.get(t, 0.0))
    return ts, np.array(price), np.array(fu), np.array(fe)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    a = ap.parse_args()

    cfgB = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                  max_liquidity_rank=15)
    cfgA = Config(lookback=14, skip=0, slippage_bps_per_side=5.0)
    start, end = runner.split_view_range("train")

    st = PointInTimeStore(a.db, read_only=True)
    resB = run_backtest(st, cfgB, start, end)
    resA = run_backtest(st, cfgA, start, end)
    st.close()

    # ---------------- 1. per-year bootstrap CIs on B ---------------------
    tsB, eqB = window(resB)
    ts_all, price, fund, fee = daily_parts(resB)
    idx_of = {t: i for i, t in enumerate(ts_all)}
    print("=== Stage 5b 1: B per-year, block-bootstrap 90% CIs ===")
    print(f"{'year':>5} {'price$':>9} {'90% CI':>20} {'excl0':>6} | "
          f"{'funding$':>9} {'90% CI':>20} {'excl0':>6} | {'sharpe':>7} {'90% CI':>16}")
    per_year = {}
    for y in (2020, 2021, 2022, 2023):
        days = [t for t in tsB if year(t) == y]
        if len(days) < 60:
            continue
        pi = np.array([price[idx_of[t]] for t in days])
        fi = np.array([fund[idx_of[t]] for t in days])
        j0 = tsB.index(days[0]); j1 = tsB.index(days[-1])
        r = metrics.daily_returns(eqB[max(j0 - 1, 0):j1 + 1])
        n = len(days)
        mb = max(2.0, n ** (1 / 3))
        rho = ac1(pi)
        mb = max(mb, 2.0 / max(1e-9, -np.log(abs(rho))) if 0 < abs(rho) < 1 else 0.0)
        ix = blocks(n, mb, 100 + y)
        cp = ci(pi[ix].sum(axis=1)); cf = ci(fi[ix].sum(axis=1))
        m = min(len(r), n)
        ixr = blocks(m, mb, 200 + y)
        rr = r[:m][ixr]
        cs = ci(rr.mean(axis=1) / rr.std(axis=1, ddof=1) * np.sqrt(metrics.ANN))
        per_year[y] = {"price": float(pi.sum()), "price_ci": cp,
                       "funding": float(fi.sum()), "funding_ci": cf,
                       "sharpe": metrics.sharpe(r), "sharpe_ci": cs,
                       "block": mb, "n": n}
        print(f"{y:>5} {pi.sum():>+9.2f} {fmt(cp):>20} {'yes' if excl(cp) else 'no':>6} | "
              f"{fi.sum():>+9.2f} {fmt(cf):>20} {'yes' if excl(cf) else 'no':>6} | "
              f"{metrics.sharpe(r):>+7.2f} {fmt2(cs):>16}")
    npos = sum(1 for y, v in per_year.items() if v["price"] > 0 and excl(v["price_ci"]))
    print(f"\n  positive years whose price-PnL CI excludes zero: {npos} of "
          f"{sum(1 for v in per_year.values() if v['price'] > 0)}")

    # ---------------- 2. drift decomposition on B ------------------------
    print("\n=== Stage 5b 2: drift decomposition on B (DIAGNOSTIC ONLY) ===")
    st2 = PointInTimeStore(a.demeaned_db, read_only=True)
    resBd = run_backtest(st2, cfgB, start, end)
    st2.close()
    _, eqBd = window(resBd)
    srB = metrics.sharpe(metrics.daily_returns(eqB))
    srBd = metrics.sharpe(metrics.daily_returns(eqBd))
    drift = srB - srBd
    frac = drift / srB if srB else float("nan")
    print(f"  Sharpe (real)     : {srB:+.3f}")
    print(f"  Sharpe (demeaned) : {srBd:+.3f}")
    print(f"  drift component   : {drift:+.3f}  ({frac:.0%} of total)")
    print(f"  vs A: 44% | vs synthetic zero-drift floor: ~18%")
    tsBd, _ = window(resBd)
    print(f"\n  per year:")
    print(f"  {'year':>5} {'SR real':>8} {'SR demean':>10} {'drift':>7} {'% of total':>11}")
    drift_year = {}
    for y in (2020, 2021, 2022, 2023):
        dA = [t for t in tsB if year(t) == y]
        dB = [t for t in tsBd if year(t) == y]
        if len(dA) < 60 or len(dB) < 60:
            continue
        i0, i1 = tsB.index(dA[0]), tsB.index(dA[-1])
        j0, j1 = tsBd.index(dB[0]), tsBd.index(dB[-1])
        s1 = metrics.sharpe(metrics.daily_returns(eqB[max(i0 - 1, 0):i1 + 1]))
        s2 = metrics.sharpe(metrics.daily_returns(eqBd[max(j0 - 1, 0):j1 + 1]))
        drift_year[y] = {"real": s1, "demeaned": s2, "drift": s1 - s2,
                         "frac": (s1 - s2) / s1 if s1 else float("nan")}
        print(f"  {y:>5} {s1:>+8.2f} {s2:>+10.2f} {s1-s2:>+7.2f} "
              f"{(s1-s2)/s1 if s1 else float('nan'):>10.0%}")

    # ---------------- 3. turnover B vs A ---------------------------------
    print("\n=== Stage 5b 3: turnover, B vs A ===")
    out_turn = {}
    for name, res in (("A", resA), ("B", resB)):
        _, eq = window(res)
        cross = adj = 0.0
        prev = None
        for rb in sorted(res.rebalances, key=lambda r: r.ts_fill):
            if prev is not None:
                for sym, (delta, price_) in rb.fills.items():
                    n_ = abs(delta) * price_
                    if (sym in prev) != (sym in rb.final_weights):
                        cross += n_
                    else:
                        adj += n_
            prev = set(rb.final_weights)
        tot = cross + adj
        ann = metrics.turnover_annualised(res.total_turnover, eq)
        out_turn[name] = {"annualised": ann, "cross": cross, "adj": adj,
                          "cross_frac": cross / tot if tot else float("nan"),
                          "fee_drag": metrics.fee_drag(res.total_fees, res.gross_pnl)}
        print(f"  {name}: {ann:>6.1f}x annualised | boundary-crossing "
              f"{cross/tot:>5.1%} | adjustment {adj/tot:>5.1%} | fee drag "
              f"{out_turn[name]['fee_drag']:.2%}")

    # ---------------- 4. payer inputs ------------------------------------
    print("\n=== Stage 5b 4: payer inputs for B ===")
    longf = sum(amt for _, _, u, _, amt in resB.funding_rows if u > 0)
    shortf = sum(amt for _, _, u, _, amt in resB.funding_rows if u < 0)
    net = eqB[-1] - cfgB.initial_capital
    print(f"  price {resB.gross_pnl:+.2f} ({resB.gross_pnl/net:.0%} of net) | "
          f"funding {resB.total_funding:+.2f} ({resB.total_funding/net:.0%})")
    print(f"  price by leg: long {resB.gross_pnl_long:+.2f} | short {resB.gross_pnl_short:+.2f}")
    print(f"  funding by leg: long {longf:+.2f} | short {shortf:+.2f}"
          f"  (A was 81% long-leg)")
    rates = np.array([r for _, _, _, r, _ in resB.funding_rows])
    print(f"  funding settlements {len(rates):,} | mean rate {rates.mean():+.6f} | "
          f"% below -0.01% {100*(rates < -1e-4).mean():.1f}%  (A: 13.2% on USDT)")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "stage5b_attribution",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "config": "B1 top-15 PIT majors",
            "per_year": {str(k): v for k, v in per_year.items()},
            "drift": {"sharpe_real": srB, "sharpe_demeaned": srBd,
                      "drift": drift, "fraction": frac,
                      "per_year": {str(k): v for k, v in drift_year.items()}},
            "turnover": out_turn,
            "payer": {"price": resB.gross_pnl, "funding": resB.total_funding,
                      "price_long": resB.gross_pnl_long,
                      "price_short": resB.gross_pnl_short,
                      "funding_long": longf, "funding_short": shortf},
            "note": "attribution/resampling of existing runs; demeaned run is "
                    "DIAGNOSTIC ONLY (full-sample means, not runnable live); "
                    "no trial consumed",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=stage5b_attribution)")


if __name__ == "__main__":
    main()
