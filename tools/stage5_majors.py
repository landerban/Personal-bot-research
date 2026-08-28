#!/usr/bin/env python3
"""
Stage 5: majors reconstruction (B1) and its USDC re-costing (B2), vs frozen (A).

  python tools/stage5_majors.py

A  = frozen uncapped config, already logged, NOT re-run here (read from disk
     by re-executing the same config -- no new trial).
B1 = top-15 PIT majors, USDT taker fees.        TRIAL 8
B2 = the IDENTICAL B1 position series re-costed at USDC taker fees. TRIAL 9

B2 is a re-costing, not a second backtest: the unit positions of B1 are held
fixed and only the fee line changes, so no ordering or path difference can
contaminate the fee comparison (STAGE5 2). The simplification is that in
reality different fees would change equity and hence later position sizing;
this isolates the fee effect instead, and says so.

Funding for B is USDT funding -- USDC funding did not exist in 2020-23. It
is a PROXY, measured at 0.86x the USDC tail in NOTES 31.1, so if anything
generous. Never presented as USDC funding.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import metrics, runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

USDT_TAKER = 0.0005      # 0.0500%
USDC_TAKER = 0.00036     # 0.0400% less the 10% BNB discount -- an ASSUMPTION
N_BOOT, CONF = 2000, 0.90


def year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def ac1(x):
    x = x - x.mean()
    d = float(x @ x)
    return float(x[:-1] @ x[1:] / d) if d > 0 else 0.0


def blocks(n, mb, seed):
    rng = np.random.default_rng(seed)
    p = 1.0 / mb
    st = rng.integers(0, n, size=(N_BOOT, n))
    jp = rng.random((N_BOOT, n)) < p
    idx = np.empty((N_BOOT, n), dtype=np.int64)
    idx[:, 0] = st[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jp[:, j], st[:, j], (idx[:, j - 1] + 1) % n)
    return idx


def ci(v):
    v = v[np.isfinite(v)]
    if len(v) < N_BOOT // 10:
        return (float("nan"), float("nan"))
    lo = (1 - CONF) / 2 * 100
    return (float(np.percentile(v, lo)), float(np.percentile(v, 100 - lo)))


def fmt(c):
    return "n/a" if any(np.isnan(c)) else f"[{c[0]:+.4f}, {c[1]:+.4f}]"


def recost(res, new_rate: float, old_rate: float = USDT_TAKER):
    """Daily equity path for the SAME positions at a different fee rate."""
    ts = res.timestamps
    fees = {t: f for t, f, _ in res.fees_by_day}
    turn = {t: v for t, _, v in res.fees_by_day}
    cum_f_old = np.cumsum([fees[t] for t in ts])
    cum_f_new = np.cumsum([turn[t] * new_rate for t in ts])
    ge = np.array(res.gross_equity)
    eq = np.array(res.equity)
    funding = eq - ge + cum_f_old            # cumulative funding per day
    return ge - cum_f_new + funding, float(cum_f_new[-1])


def series(res, eq_override=None):
    """(timestamps, daily returns) over the STRATEGY WINDOW -- from the first
    fill onward, the basis every prior figure in this project uses. Using the
    full window instead folds in ~200 pre-fill zero days and reports a
    different Sharpe for the same run (0.744 vs 0.796 for A)."""
    eq_full = np.asarray(res.equity if eq_override is None else eq_override)
    if not res.rebalances:
        return list(res.timestamps)[1:], metrics.daily_returns(eq_full)
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return list(res.timestamps)[lo + 1:], metrics.daily_returns(eq_full[lo:])


def paired(rA, rB, label, seed, mb=None):
    n = len(rA)
    rho = ac1(rB - rA)
    mb = mb or max(2.0, n ** (1 / 3))
    from_rho = 2.0 / max(1e-9, -np.log(abs(rho))) if 0 < abs(rho) < 1 else 0.0
    mb = max(mb, from_rho)
    idx = blocks(n, mb, seed)
    a, b = rA[idx], rB[idx]
    sh = (b.mean(axis=1) / b.std(axis=1, ddof=1)
          - a.mean(axis=1) / a.std(axis=1, ddof=1)) * np.sqrt(metrics.ANN)
    pt = metrics.sharpe(rB) - metrics.sharpe(rA)
    c = ci(sh)
    verdict = ("ABOVE zero" if not any(np.isnan(c)) and c[0] > 0 else
               "BELOW zero" if not any(np.isnan(c)) and c[1] < 0 else "straddles zero")
    print(f"  {label:<14} {pt:>+8.4f}   90% CI {fmt(c):<24} {verdict}"
          f"   (block {mb:.1f}d, diff rho {rho:+.4f})")
    return {"point": pt, "ci": c, "verdict": verdict, "block": mb, "rho": rho}


def describe(name, res, eq_override=None, fees_override=None):
    eq_full = np.array(res.equity if eq_override is None else eq_override)
    ts, r = series(res, eq_override)
    # window-consistent equity path: everything below is on the same basis
    i = res.timestamps.index(res.rebalances[0].ts_fill) if res.rebalances else 1
    eq = eq_full[max(i - 1, 0):]
    tot_fees = res.total_fees if fees_override is None else fees_override
    net = eq_full[-1] - res.config.initial_capital
    gp = res.gross_pnl
    fund = net - gp + tot_fees
    lev = np.array([rb.realised_gross_leverage for rb in res.rebalances])
    peaks = np.maximum.accumulate(eq)
    print(f"\n--- {name} ---")
    print(f"  sharpe {metrics.sharpe(r):+.3f}  CI {fmt(metrics.sharpe_bootstrap_ci(r))}"
          f"  ann_ret {metrics.ann_return(eq):+.2%}  vol {metrics.ann_vol(r):.2%}"
          f"  maxDD {float(((peaks-eq)/peaks).max()):.2%}")
    print(f"  price {gp:+.2f} | fees {tot_fees:.2f} | funding {fund:+.2f} | net {net:+.2f}"
          f"  -> funding {fund/net:.1%} of net" if net else "")
    print(f"  fee drag {tot_fees/gp:.2%} of gross | turnover "
          f"{metrics.turnover_annualised(res.total_turnover, eq):.1f}x | "
          f"rebalances {len(res.rebalances)} | active {int((r!=0).sum())}/{len(r)}")
    print(f"  leverage med {np.median(lev):.2f} p95 {np.percentile(lev,95):.2f} | "
          f"skips {len(res.skips)}")
    return {"sharpe": metrics.sharpe(r), "ann_return": metrics.ann_return(eq),
            "max_dd": float(((peaks - eq) / peaks).max()), "price": gp,
            "fees": tot_fees, "funding": fund, "net": float(net),
            "n_rebalances": len(res.rebalances)}


def per_year(name, res, eq_override=None, fees_override=None):
    eq = np.array(res.equity if eq_override is None else eq_override)
    ts = res.timestamps
    fees = {t: f for t, f, _ in res.fees_by_day}
    rate_scale = 1.0 if fees_override is None else fees_override / res.total_fees
    print(f"\n  per-year, {name} (STAGE5 5 / 19.3 format)")
    print(f"  {'year':>5} {'sharpe':>7} {'net$':>9} {'price$':>9} {'funding$':>9} "
          f"{'fees$':>8}")
    out = {}
    for y in sorted({year(t) for t in ts}):
        idx = [i for i, t in enumerate(ts) if year(t) == y]
        if len(idx) < 2:
            continue
        sub = eq[idx[0]:idx[-1] + 1]
        r = metrics.daily_returns(sub)
        price = sum(sum(res.pnl_by_symbol_day[i].values()) for i in idx)
        f = sum(fees[ts[i]] for i in idx) * rate_scale
        net = float(sub[-1] - sub[0])
        out[y] = {"sharpe": metrics.sharpe(r), "net": net, "price": price,
                  "funding": net - price + f, "fees": f}
        print(f"  {y:>5} {out[y]['sharpe']:>7.2f} {net:>+9.2f} {price:>+9.2f} "
              f"{out[y]['funding']:>+9.2f} {f:>8.2f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()
    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)

    cfgA = Config(lookback=14, skip=0, slippage_bps_per_side=5.0)
    cfgB = Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                  max_liquidity_rank=15)
    print("running A (frozen, already logged -- not a new trial)...", flush=True)
    resA = run_backtest(store, cfgA, start, end)
    print("running B1 (top-15 PIT majors, USDT fees) -- TRIAL 8...", flush=True)
    resB = run_backtest(store, cfgB, start, end)
    store.close()

    eqB2, feesB2 = recost(resB, USDC_TAKER)
    print(f"\nB2 = B1 re-costed at USDC taker {USDC_TAKER:.5%} "
          f"(vs USDT {USDT_TAKER:.5%}); positions IDENTICAL, only the fee line moves")
    print(f"   fees {resB.total_fees:.2f} -> {feesB2:.2f} "
          f"(saving ${resB.total_fees - feesB2:.2f} on ${resB.total_turnover:,.0f} turnover)")

    sA = describe("A  frozen, full USDT universe", resA)
    sB1 = describe("B1 top-15 PIT majors, USDT fees", resB)
    sB2 = describe("B2 same positions, USDC fees (funding is a USDT PROXY)",
                   resB, eq_override=eqB2, fees_override=feesB2)
    yA = per_year("A", resA)
    yB1 = per_year("B1", resB)
    yB2 = per_year("B2", resB, eq_override=eqB2, fees_override=feesB2)

    tA, rA = series(resA)
    tB, rB = series(resB)
    _, rB2 = series(resB, eqB2)
    if tA != tB:
        common = sorted(set(tA) & set(tB))
        ia = {t: i for i, t in enumerate(tA)}
        ib = {t: i for i, t in enumerate(tB)}
        rA = np.array([rA[ia[t]] for t in common])
        rB = np.array([rB[ib[t]] for t in common])
        rB2 = np.array([rB2[ib[t]] for t in common])
        print(f"\n(aligned on {len(common):,} common days; A had {len(tA):,}, B {len(tB):,})")

    print(f"\n=== Stage 5 5: paired bootstraps, {CONF:.0%} CI, {N_BOOT:,} resamples ===")
    p1 = paired(rA, rB, "B1 - A", 11)
    p2 = paired(rB, rB2, "B2 - B1", 12)
    p3 = paired(rA, rB2, "B2 - A", 13)

    print(f"\n=== BRANCH (NOTES 33.3, fixed in advance) ===")
    c = p1["ci"]
    if not any(np.isnan(c)) and c[0] > 0:
        branch = "ONE: majors-only helps out of the noise -> strong basis to validate B"
    elif not any(np.isnan(c)) and c[1] < 0:
        branch = "THREE: majors-only HURTS on train -> do not validate B"
    else:
        branch = ("TWO: CI straddles zero"
                  + (", point estimate positive" if p1["point"] > 0 else ", point estimate negative")
                  + " -> consistent with helping, NOT established")
    print(f"  B1 - A = {p1['point']:+.4f}, CI {fmt(c)}")
    print(f"  --> BRANCH {branch}")
    pa = " -> ".join(f"{yA[y]['price']:+.0f}" for y in sorted(yA))
    pb = " -> ".join(f"{yB1[y]['price']:+.0f}" for y in sorted(yB1))
    print("")
    print(f"  A  price PnL by year: {pa}")
    print(f"  B1 price PnL by year: {pb}")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "stage5_majors",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "usdt_taker": USDT_TAKER, "usdc_taker": USDC_TAKER,
            "A": sA, "B1": sB1, "B2": sB2,
            "per_year": {"A": {str(k): v for k, v in yA.items()},
                         "B1": {str(k): v for k, v in yB1.items()},
                         "B2": {str(k): v for k, v in yB2.items()}},
            "paired": {"B1_minus_A": p1, "B2_minus_B1": p2, "B2_minus_A": p3},
            "branch": branch,
            "note": "B2 is a re-costing of B1's identical positions; funding is a "
                    "USDT proxy, never USDC funding",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=stage5_majors)")


if __name__ == "__main__":
    main()
