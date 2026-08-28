#!/usr/bin/env python3
"""
Stage 3d Part A: paired bootstrap on the rank-100 cap's effect.

  python tools/paired_cap_test.py

The standalone capped CI ([+0.23, +1.85]) contains the uncapped Sharpe
(0.796), so it cannot reject "the cap did nothing". But both runs cover the
SAME days, so their shared market noise cancels in the difference. This
resamples the difference series directly, which is far tighter.

Consumes NO trial budget: both runs already exist and nothing new is
configured. The reading was fixed in NOTES 26 and committed before this ran.

Never bootstraps the two runs independently and subtracts point estimates --
that would reintroduce exactly the common variance the pairing removes.
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

N_BOOT = 2000
CONF = 0.90


def year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def ac1(x: np.ndarray) -> float:
    x = x - x.mean()
    d = float(x @ x)
    return float(x[:-1] @ x[1:] / d) if d > 0 else 0.0


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


def fmt(c) -> str:
    if any(np.isnan(c)):
        return "n/a"
    return f"[{c[0]:+.4f}, {c[1]:+.4f}]"


def above_zero(c) -> bool:
    return not any(np.isnan(c)) and c[0] > 0


def daily_series(res):
    """(timestamps, daily returns) over the strategy window."""
    ts, eq = metrics.strategy_window(res)
    return list(ts)[1:], metrics.daily_returns(eq)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    a = ap.parse_args()

    base = dict(lookback=14, skip=0, slippage_bps_per_side=a.slippage_bps)
    store = PointInTimeStore(a.db, read_only=True)
    start, end = runner.split_view_range("train")
    res_cap = run_backtest(store, Config(**base, max_liquidity_rank=100), start, end)
    res_unc = run_backtest(store, Config(**base), start, end)
    store.close()

    t_cap, r_cap = daily_series(res_cap)
    t_unc, r_unc = daily_series(res_unc)

    # ---- alignment: dates must match exactly, or stop --------------------
    if t_cap != t_unc or len(r_cap) != len(r_unc):
        sys.exit(f"STOP: series do not align -- capped {len(t_cap)} days, "
                 f"uncapped {len(t_unc)} days (STAGE3D 1.1)")
    print(f"=== Stage 3d Part A: paired bootstrap | 5bps, +1min | train ===")
    print(f"aligned on {len(t_cap):,} identical daily observations "
          f"({datetime.fromtimestamp(t_cap[0]/1000, tz=timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(t_cap[-1]/1000, tz=timezone.utc):%Y-%m-%d})")

    d = r_cap - r_unc
    nz = float((d != 0).mean())
    rho_d = ac1(d)
    rho_lvl = ac1(r_cap)
    n = len(d)
    mean_block = max(2.0, n ** (1 / 3))
    from_rho = 2.0 / max(1e-9, -np.log(abs(rho_d))) if 0 < abs(rho_d) < 1 else 0.0
    mean_block = max(mean_block, from_rho)
    print(f"lag-1 autocorr: difference series {rho_d:+.4f} "
          f"(level series {rho_lvl:+.4f} -- recomputed, not reused)")
    print(f"block length max(n^(1/3)={n**(1/3):.1f}, from rho={from_rho:.1f}) "
          f"= {mean_block:.1f} days | {N_BOOT:,} resamples | {CONF:.0%} CI")
    print(f"days with a non-zero difference: {nz:.1%} "
          f"({int((d != 0).sum()):,} of {n:,}) -- the cap is inert on the rest")

    def paired(mask_years=None, label="", seed=1):
        if mask_years is None:
            sel = np.ones(n, dtype=bool)
        else:
            sel = np.array([year(t) in mask_years for t in t_cap])
        c, u = r_cap[sel], r_unc[sel]
        m = len(c)
        if m < 60:
            print(f"  {label:<22} too few observations ({m})")
            return None
        idx = block_indices(m, mean_block, N_BOOT, seed)
        cs, us = c[idx], u[idx]
        # paired: the SAME resampled days for both series
        sh = (cs.mean(axis=1) / cs.std(axis=1, ddof=1) * np.sqrt(metrics.ANN)
              - us.mean(axis=1) / us.std(axis=1, ddof=1) * np.sqrt(metrics.ANN))
        dd = cs - us
        mean_d = dd.mean(axis=1)
        ann_d = ((1 + cs.mean(axis=1)) ** metrics.ANN
                 - (1 + us.mean(axis=1)) ** metrics.ANN)
        out = {
            "n_days": m,
            "sharpe_diff_point": float(metrics.sharpe(c) - metrics.sharpe(u)),
            "sharpe_diff_ci": ci(sh),
            "mean_daily_diff_point": float((c - u).mean()),
            "mean_daily_diff_ci": ci(mean_d),
            "ann_return_diff_ci": ci(ann_d),
            "sharpe_capped": float(metrics.sharpe(c)),
            "sharpe_uncapped": float(metrics.sharpe(u)),
        }
        print(f"\n  {label} ({m:,} days)")
        print(f"    Sharpe            capped {out['sharpe_capped']:+.3f} vs "
              f"uncapped {out['sharpe_uncapped']:+.3f}")
        print(f"    Sharpe difference {out['sharpe_diff_point']:+.4f}  "
              f"90% CI {fmt(out['sharpe_diff_ci'])}  "
              f"{'ABOVE ZERO' if above_zero(out['sharpe_diff_ci']) else 'straddles zero'}")
        print(f"    mean daily diff   {out['mean_daily_diff_point']:+.6f}  "
              f"90% CI {fmt(out['mean_daily_diff_ci'])}")
        print(f"    ann return diff   90% CI {fmt(out['ann_return_diff_ci'])}")
        return out

    full = paired(None, "FULL WINDOW 2020-2023", seed=1)
    sub = paired({2021, 2022}, "2021-22 ONLY (excl. 2023)", seed=2)
    y23 = paired({2023}, "2023 ALONE", seed=3)

    print(f"\n=== BRANCH (NOTES 26, fixed in advance) ===")
    full_ok = above_zero(full["sharpe_diff_ci"])
    sub_ok = sub is not None and above_zero(sub["sharpe_diff_ci"])
    print(f"  full-window Sharpe-difference CI above zero : {full_ok}")
    print(f"  2021-22 subset CI above zero                : {sub_ok}")
    if full_ok and sub_ok:
        branch = ("ONE: the cap's effect is established (and does not rest on "
                  "2023 alone) -> proceed to Part B")
    elif full_ok and not sub_ok:
        branch = ("THREE: above zero only with 2023 included; the 2021-22 subset "
                  "straddles zero -> WEAK, rests on one year. STOP and report; "
                  "the decision moves to the user")
    else:
        branch = ("TWO: the Sharpe-difference CI straddles zero -> NOT established. "
                  "STOP. Do not spend the last trial validating the cap")
    print(f"  --> BRANCH {branch}")
    print("\nCONFOUND (stated, not tested around): the cap binds on 100% of 2023")
    print("days and 2023 has the largest 101+ share (22.1%). Removing the tail")
    print("necessarily helps most where the tail is largest, so 2023's dominance")
    print("is the mechanism restated, not independent evidence for it.")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "paired_cap_test",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "split": "train", "slippage_bps": a.slippage_bps,
            "n_days": n, "nonzero_diff_fraction": nz,
            "lag1_autocorr_difference": rho_d, "lag1_autocorr_level": rho_lvl,
            "mean_block_days": mean_block, "n_boot": N_BOOT, "confidence": CONF,
            "full_window": full, "subset_2021_22": sub, "year_2023": y23,
            "branch": branch,
            "note": "paired resampling of two existing runs; no trial consumed",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=paired_cap_test)")


if __name__ == "__main__":
    main()
