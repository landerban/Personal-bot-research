#!/usr/bin/env python3
"""
Stage 9: does a rank buffer (hysteresis) beat the frozen b=0 config on train?

  python tools/stage9_rankbuffer.py

Three trials (b=1,2,3) against the frozen 10%/$800 deployment config. The
widths and the selection rule were fixed in NOTES 45 and committed before any
of this ran; nothing here is adjusted after seeing a number.

A buffer WINS only if ALL THREE hold (NOTES 45.4):
  1. net Sharpe improves on b=0 with a PAIRED-bootstrap 90% CI excluding zero
  2. turnover falls AND the boundary-crossing share falls with it
  3. drift fraction < 30% and demeaned Sharpe > 0   <- the 41 disqualifier

Smallest passing b wins, NOT the highest Sharpe. If none passes, b=0 stands.

The b=0 baseline is re-run (no trial -- same config, already logged) purely so
the paired bootstrap has its daily series on identical dates. It MUST
reproduce NOTES 43.6 or this stops: a moved baseline invalidates every
comparison.

Train only. 2024 untouched. Holdout sealed.
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

USDC_TAKER = 0.00036
N_BOOT = 2000
CONF = 0.90
BUFFERS = (1, 2, 3)
DRIFT_CAP = 0.30
DD_CAP = 0.20          # the cap the vol was selected under (NOTES 43.3)
# NOTES 43.6, the 10% @ $800 train row. The b=0 re-run must reproduce it.
REF0 = {"sharpe": 0.8351, "n_rebalances": 1241, "skip_rate": 0.2155,
        "max_dd": 0.1703, "realised_vol": 0.0928, "demeaned_sharpe": 0.6513}


def d(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def ac1(x: np.ndarray) -> float:
    x = x - x.mean()
    den = float(x @ x)
    return float(x[:-1] @ x[1:] / den) if den > 0 else 0.0


def block_indices(n: int, mean_block: float, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    starts = rng.integers(0, n, size=(n_boot, n))
    jumps = rng.random((n_boot, n)) < p
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jumps[:, j], starts[:, j], (idx[:, j - 1] + 1) % n)
    return idx


def ci(v: np.ndarray) -> tuple[float, float]:
    v = v[np.isfinite(v)]
    if len(v) < N_BOOT // 10:
        return (float("nan"), float("nan"))
    lo = (1 - CONF) / 2 * 100
    return (float(np.percentile(v, lo)), float(np.percentile(v, 100 - lo)))


def fmt_ci(c) -> str:
    return "n/a" if any(np.isnan(c)) else f"[{c[0]:+.4f}, {c[1]:+.4f}]"


def above_zero(c) -> bool:
    return not any(np.isnan(c)) and c[0] > 0


def window(res):
    i = res.timestamps.index(res.rebalances[0].ts_fill)
    lo = max(i - 1, 0)
    return res.timestamps[lo:], np.array(res.equity[lo:])


def daily_series(res):
    ts, eq = window(res)
    return list(ts)[1:], metrics.daily_returns(eq)


def turnover_split(res) -> tuple[float, float]:
    """(boundary-crossing $, adjustment $) -- the Stage 5b 3 measure."""
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
    return cross, adj


def usdc_fees(res) -> float:
    return float(sum(turn * USDC_TAKER for _, _, turn in res.fees_by_day))


def measure(res, resd) -> dict:
    ts, eq = window(res)
    r = metrics.daily_returns(eq)
    _, eqd = window(resd)
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks
    sr = metrics.sharpe(r)
    srd = metrics.sharpe(metrics.daily_returns(eqd))
    cross, adj = turnover_split(res)
    tot = cross + adj
    fu, fc = res.total_fees, usdc_fees(res)
    return {
        "sharpe": sr, "demeaned_sharpe": srd, "drift": sr - srd,
        "drift_fraction": (sr - srd) / sr if sr else float("nan"),
        "ann_return": metrics.ann_return(eq),
        "realised_vol": metrics.ann_vol(r),
        "max_dd": float(dd.max()), "dd_date": d(ts[int(np.argmax(dd))]),
        "n_rebalances": len(res.rebalances),
        "skip_rate": len(res.skips) / max(res.n_scheduled, 1),
        "skips_by_reason": res.skip_counts(),
        "turnover_ann": metrics.turnover_annualised(res.total_turnover, eq),
        "turnover_total": float(res.total_turnover),
        "cross": cross, "adj": adj,
        "cross_frac": cross / tot if tot else float("nan"),
        "price_pnl": res.gross_pnl, "funding_pnl": res.total_funding,
        "fees_usdt": fu, "fees_usdc": fc,
        "fee_drag_usdt": metrics.fee_drag(fu, res.gross_pnl),
        "fee_drag_usdc": metrics.fee_drag(fc, res.gross_pnl),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--demeaned-db", default=str(ROOT / "xsmom_demeaned.db"))
    ap.add_argument("--capital", type=float, default=800.0)
    ap.add_argument("--vol-target", type=float, default=0.10)
    ap.add_argument("--purpose", default="stage9-rankbuffer")
    ap.add_argument("--no-log-trial", action="store_true")
    a = ap.parse_args()

    start, end = runner.split_view_range("train")
    assert d(end) == "2023-12-31", f"not the train window: {d(end)}"

    def cfg_for(b):
        return Config(lookback=14, skip=0, slippage_bps_per_side=5.0,
                      max_liquidity_rank=15, vol_target=a.vol_target,
                      initial_capital=a.capital, rank_buffer=b)

    print(f"=== Stage 9: rank buffer on B @ {a.vol_target:.0%} @ ${a.capital:.0f} "
          f"| train {d(start)} -> {d(end)} ===")
    print(f"    b=0 baseline re-run (no trial) + b={BUFFERS} (3 trials)")
    print("    2024 untouched; holdout sealed\n")

    store = PointInTimeStore(a.db, read_only=True)
    dstore = PointInTimeStore(a.demeaned_db, read_only=True)
    runs, series = {}, {}
    for b in (0,) + BUFFERS:
        print(f"running b={b} ...", flush=True)
        store.reset_clock()
        res = run_backtest(store, cfg_for(b), start, end)
        dstore.reset_clock()
        resd = run_backtest(dstore, cfg_for(b), start, end)
        runs[b] = measure(res, resd)
        series[b] = daily_series(res)
        if b and not a.no_log_trial:
            runner.log_trial(cfg_for(b), "train", f"{a.purpose}-b{b}",
                             runner.summarise(res))
    store.close()
    dstore.close()

    # ---- baseline determinism check (NOTES 45.3) ------------------------
    print("\n=== baseline check: b=0 must reproduce NOTES 43.6 ===")
    ok = True
    for k, want in REF0.items():
        got = runs[0][k]
        match = abs(got - want) <= (1 if k == "n_rebalances" else 0.005)
        ok &= match
        print(f"  {k:<18} got {got:>10.4f}   43.6 says {want:>10.4f}   "
              f"{'ok' if match else 'MISMATCH'}")
    if not ok:
        sys.exit("STOP: the buffer code moved the b=0 path. Every comparison "
                 "would be against a shifted baseline (NOTES 45.3).")

    # ---- the table (NOTES 45.5) -----------------------------------------
    print(f"\n=== per-buffer results | train | b=0 is the frozen config ===")
    print(f"{'b':>2} {'sharpe':>7} {'demean':>7} {'drift':>6} {'annret':>7} "
          f"{'maxDD':>7} {'vol':>6} {'skip':>7} {'rebal':>6} {'turn':>6} "
          f"{'cross%':>7} {'drag U':>7} {'drag C':>7}")
    for b in (0,) + BUFFERS:
        r = runs[b]
        print(f"{b:>2} {r['sharpe']:>+7.3f} {r['demeaned_sharpe']:>+7.3f} "
              f"{r['drift_fraction']:>6.0%} {r['ann_return']:>7.2%} "
              f"{r['max_dd']:>7.2%} {r['realised_vol']:>6.2%} "
              f"{r['skip_rate']:>7.2%} {r['n_rebalances']:>6} "
              f"{r['turnover_ann']:>6.1f} {r['cross_frac']:>7.1%} "
              f"{r['fee_drag_usdt']:>7.1%} {r['fee_drag_usdc']:>7.1%}")
    print("  turn = annualised turnover multiple; cross% = boundary-crossing "
          "share of traded notional")
    print("  drag U / drag C = fees as a share of price PnL at USDT / USDC "
          "taker rates")

    print(f"\n{'b':>2} {'price PnL':>11} {'funding PnL':>12} {'fees USDT':>10} "
          f"{'fees USDC':>10}")
    for b in (0,) + BUFFERS:
        r = runs[b]
        print(f"{b:>2} {r['price_pnl']:>+11.2f} {r['funding_pnl']:>+12.2f} "
              f"{r['fees_usdt']:>10.2f} {r['fees_usdc']:>10.2f}")

    # ---- paired bootstraps vs b=0 (NOTES 45.4 condition 1) --------------
    t0, r0 = series[0]
    print(f"\n=== PAIRED bootstrap vs b=0 | {N_BOOT:,} resamples | {CONF:.0%} CI ===")
    print("    the same resampled days for both series -- never two independent")
    print("    bootstraps subtracted (NOTES 26)")
    paired = {}
    for i, b in enumerate(BUFFERS, start=1):
        tb, rb = series[b]
        if tb != t0 or len(rb) != len(r0):
            sys.exit(f"STOP: b={b} series does not align with b=0 "
                     f"({len(tb)} vs {len(t0)} days)")
        n = len(r0)
        diff = rb - r0
        rho = ac1(diff)
        mean_block = max(2.0, n ** (1 / 3))
        if 0 < abs(rho) < 1:
            mean_block = max(mean_block, 2.0 / max(1e-9, -np.log(abs(rho))))
        idx = block_indices(n, mean_block, N_BOOT, seed=100 + i)
        bs, b0 = rb[idx], r0[idx]
        sh = (bs.mean(axis=1) / bs.std(axis=1, ddof=1)
              - b0.mean(axis=1) / b0.std(axis=1, ddof=1)) * np.sqrt(metrics.ANN)
        md = (bs - b0).mean(axis=1)
        ar = ((1 + bs.mean(axis=1)) ** metrics.ANN
              - (1 + b0.mean(axis=1)) ** metrics.ANN)
        paired[b] = {
            "n_days": n, "nonzero_diff_fraction": float((diff != 0).mean()),
            "lag1_autocorr_difference": rho, "mean_block_days": mean_block,
            "sharpe_diff_point": runs[b]["sharpe"] - runs[0]["sharpe"],
            "sharpe_diff_ci": ci(sh),
            "mean_daily_diff_ci": ci(md), "ann_return_diff_ci": ci(ar),
        }
        p = paired[b]
        print(f"\n  b={b}  ({n:,} aligned days; buffer changes the book on "
              f"{p['nonzero_diff_fraction']:.1%} of them)")
        print(f"    block length {mean_block:.1f}d (lag-1 autocorr of the "
              f"difference {rho:+.4f})")
        print(f"    Sharpe difference {p['sharpe_diff_point']:+.4f}  "
              f"90% CI {fmt_ci(p['sharpe_diff_ci'])}  "
              f"{'ABOVE ZERO' if above_zero(p['sharpe_diff_ci']) else 'straddles zero'}")
        print(f"    mean daily diff   90% CI {fmt_ci(p['mean_daily_diff_ci'])}")
        print(f"    ann return diff   90% CI {fmt_ci(p['ann_return_diff_ci'])}")

    # ---- the rule (NOTES 45.4) ------------------------------------------
    print(f"\n=== SELECTION (NOTES 45.4, all three, fixed before the run) ===")
    print(f"  1. paired Sharpe-difference 90% CI excludes zero (above it)")
    print(f"  2. turnover falls AND boundary-crossing share falls")
    print(f"  3. drift fraction < {DRIFT_CAP:.0%} and demeaned Sharpe > 0")
    passing = []
    for b in BUFFERS:
        r, p = runs[b], paired[b]
        c1 = above_zero(p["sharpe_diff_ci"])
        c2 = (r["turnover_ann"] < runs[0]["turnover_ann"]
              and r["cross_frac"] < runs[0]["cross_frac"])
        c3 = r["drift_fraction"] < DRIFT_CAP and r["demeaned_sharpe"] > 0
        fails = []
        if not c1:
            fails.append(f"Sharpe CI {fmt_ci(p['sharpe_diff_ci'])}")
        if not c2:
            fails.append(
                f"turnover {r['turnover_ann']:.1f} vs {runs[0]['turnover_ann']:.1f}"
                f", cross {r['cross_frac']:.1%} vs {runs[0]['cross_frac']:.1%}")
        if not c3:
            fails.append(f"drift {r['drift_fraction']:.0%}"
                         + ("" if r["demeaned_sharpe"] > 0
                            else f" / demeaned SR {r['demeaned_sharpe']:+.3f}"))
        allok = c1 and c2 and c3
        print(f"  b={b}  {'1 ok' if c1 else '1 NO'}  {'2 ok' if c2 else '2 NO'}  "
              f"{'3 ok' if c3 else '3 NO'}   "
              f"{'ALL THREE' if allok else 'fails: ' + '; '.join(fails)}")
        if allok:
            passing.append(b)

    print()
    if passing:
        chosen = min(passing)              # SMALLEST passing b, not the best
        verdict = (
            f"BUFFER b={chosen} WINS. Smallest b satisfying all three "
            f"(passing set {passing}) -- chosen by NOTES 45.4 minimal "
            f"intervention, NOT by Sharpe. It is a NEW CONFIG and cannot "
            f"inherit 44's 2024 validation: it needs its own single validate "
            f"on 2024 (trial 15 -> 16) before it is holdout-eligible. That "
            f"validate is NOT run in this stage (NOTES 45.7).")
    else:
        chosen = None
        drift_rise = [b for b in BUFFERS
                      if runs[b]["drift_fraction"] > runs[0]["drift_fraction"]]
        lifted = [b for b in BUFFERS if runs[b]["sharpe"] > runs[0]["sharpe"]]
        trap = [b for b in lifted
                if runs[b]["drift_fraction"] >= DRIFT_CAP
                or runs[b]["demeaned_sharpe"] <= 0]
        if trap:
            verdict = (
                f"NO BUFFER PASSES, and b={trap} lifted Sharpe while the "
                f"mechanism degraded -- the 41 trap (a gain bought by holding "
                f"drifting names, not by saving turnover). b=0 STANDS.")
        else:
            verdict = (
                f"NO BUFFER PASSES. b=0 IS THE DEPLOYMENT CONFIG and the last "
                f"strategy question is closed."
                + (f" Drift rose at b={drift_rise} but stayed inside the band."
                   if drift_rise else ""))
    print(f"  --> {verdict}")

    print(f"\n  Holdout sealed and untouched. 2024 not run in this stage.")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "stage9_rankbuffer",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "capital": a.capital, "vol_target": a.vol_target,
            "buffers": list(BUFFERS), "n_boot": N_BOOT, "confidence": CONF,
            "drift_cap": DRIFT_CAP, "runs": {str(b): runs[b] for b in runs},
            "paired": {str(b): paired[b] for b in paired},
            "passing": passing, "chosen": chosen, "verdict": verdict,
            "note": "train-only buffer comparison; b=0 re-run is a baseline, "
                    "not a trial; 2024 untouched; holdout sealed",
        }) + "\n")
    print(f"  diagnostics appended (kind=stage9_rankbuffer)")


if __name__ == "__main__":
    main()
