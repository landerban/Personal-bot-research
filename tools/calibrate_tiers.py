#!/usr/bin/env python3
"""
Stage 3f 2: calibrate the Tier 1 / Tier 2 reference bands on TRAIN.

  python tools/calibrate_tiers.py

A tracking test without a null distribution is unreadable -- you cannot tell
whether validate's deviation is anomalous without knowing what normal looks
like. This produces that reference, per calendar year, for the UNCAPPED
frozen config of NOTES 19.5.

No new configuration, no trial: it replays an already-logged config to
materialise the per-day traces (which are in-memory only) and reads the
Tier 2 intervals from the bootstrap already logged in diagnostics.jsonl.
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


def year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    cfg = Config(lookback=14, skip=0, slippage_bps_per_side=5.0)   # uncapped frozen
    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)
    res = run_backtest(store, cfg, start, end)
    v_end = store.view_as_of(end)
    bclose = {b.close_time: b.close for b in v_end.klines(BTC, "1d", limit=5000)}
    store.close()

    ts, eq = metrics.strategy_window(res)
    ts = list(ts)
    years = sorted({year(t) for t in ts})

    print("=== Stage 3f 2: TRAIN reference bands | uncapped frozen config "
          "lb14/skip0 @5bps/+1min ===\n")

    # ------------------------------------------------ Tier 1, per year
    rows = {}
    for y in years:
        idx = [i for i, t in enumerate(ts) if year(t) == y]
        if len(idx) < 2:
            continue
        sub = np.array(eq[idx[0]:idx[-1] + 1])
        rets = metrics.daily_returns(sub)
        bt = np.array([bclose.get(t, np.nan) for t in ts[idx[0]:idx[-1] + 1]], float)
        br = np.diff(bt) / bt[:-1]
        n = min(len(rets), len(br))
        ok = np.isfinite(rets[:n]) & np.isfinite(br[:n])
        beta = (float(np.cov(rets[:n][ok], br[:n][ok])[0, 1] / np.var(br[:n][ok]))
                if ok.sum() > 10 else float("nan"))
        lev = [rb.realised_gross_leverage for rb in res.rebalances
               if year(rb.ts_fill) == y]
        peaks = np.maximum.accumulate(sub)
        dd = float(((peaks - sub) / peaks).max())
        n_sk = sum(1 for t2, _, _ in res.skips if year(t2) == y)
        n_rb = len(lev)
        rows[y] = {
            "beta": beta,
            "lev_median": float(np.median(lev)) if lev else float("nan"),
            "lev_p95": float(np.percentile(lev, 95)) if lev else float("nan"),
            "max_dd": dd,
            "active_frac": float((rets != 0).mean()),
            "skip_rate": n_sk / max(n_sk + n_rb, 1),
            "n_rebalances": n_rb,
            "n_skips": n_sk,
        }

    print("TIER 1 -- structural invariants, per calendar year")
    print(f"{'year':>5} {'beta':>8} {'lev med':>8} {'lev p95':>8} {'maxDD':>7} "
          f"{'active':>7} {'skip rate':>10} {'rebal':>6}")
    for y, r in rows.items():
        print(f"{y:>5} {r['beta']:>+8.3f} {r['lev_median']:>8.2f} {r['lev_p95']:>8.2f} "
              f"{r['max_dd']:>7.2%} {r['active_frac']:>7.1%} {r['skip_rate']:>10.1%} "
              f"{r['n_rebalances']:>6}")

    def rng(k):
        v = [r[k] for r in rows.values() if np.isfinite(r[k])]
        return min(v), max(v)

    print("\nobserved TRAIN ranges (these become the tolerance bands in 30.1):")
    for k, label, fmt in (("beta", "realised beta to BTC", "{:+.3f}"),
                          ("lev_median", "realised gross leverage (median)", "{:.2f}"),
                          ("lev_p95", "realised gross leverage (p95)", "{:.2f}"),
                          ("max_dd", "max drawdown (within year)", "{:.2%}"),
                          ("active_frac", "active-days fraction", "{:.1%}"),
                          ("skip_rate", "skip rate", "{:.1%}")):
        lo, hi = rng(k)
        print(f"  {label:<34} [{fmt.format(lo)}, {fmt.format(hi)}]")

    peaks = np.maximum.accumulate(eq)
    gdd = float(((peaks - eq) / peaks).max())
    _, eqw = metrics.strategy_window(res)
    print(f"  {'max drawdown (whole window)':<34} {gdd:.2%}  "
          f"(kill switch {KILL_SWITCH:.0%})")
    print(f"  {'active days, whole window':<34} "
          f"{int((metrics.daily_returns(eqw) != 0).sum()):,} of "
          f"{len(metrics.daily_returns(eqw)):,}")

    # dollar-tilt identity: exact by construction, verified over every rebalance
    worst = max(abs(sum(rb.final_weights.values()) - rb.vol_scale * (1 - rb.beta_scale))
                for rb in res.rebalances)
    print(f"  {'dollar tilt |sum(w) - k(1-s)|':<34} {worst:.2e}  "
          f"(exact by construction; Test 12)")

    # composition
    net = res.gross_pnl - res.total_fees + res.total_funding
    print(f"\ncomposition: price {res.gross_pnl:+.2f} | fees {res.total_fees:.2f} | "
          f"funding {res.total_funding:+.2f} | net {net:+.2f} -> funding is "
          f"{res.total_funding / net:.1%} of net")

    # ------------------------------------------------ Tier 2, from the bootstrap
    d = [json.loads(l) for l in open(runner.DIAGNOSTICS_PATH, encoding="utf-8")
         if l.strip()]
    bb = [x for x in d if x.get("kind") == "bucket_bootstrap"][-1]
    print("\nTIER 2 -- price PnL per position-day, train 90% CIs (from 24.1)")
    print(f"{'scope':<28} {'point':>10} {'90% CI':>24}")
    for k, lab in (("1-30", "pooled top-30 (4 years)"),
                   ("101+", "pooled 101+ (3 years)")):
        c = bb["pooled"][k]
        print(f"  {lab:<26} {c['point']:>+10.4f}   [{c['ci'][0]:+.4f}, {c['ci'][1]:+.4f}]")
    print("\n  per-year cells:")
    for key, c in sorted(bb["cells"].items()):
        if not c.get("available"):
            print(f"    {key:<14} n/a (universe never reached this rank)")
            continue
        print(f"    {key:<14} {c['point']:>+9.4f}   "
              f"[{c['ci'][0]:+.4f}, {c['ci'][1]:+.4f}]   "
              f"{'excl 0' if c['excludes_zero'] else 'straddles 0'}")

    out = {"ts": int(time.time()), "kind": "tier_calibration",
           "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
           "config_hash": runner.config_hash(cfg), "split": "train",
           "tier1_by_year": {str(k): v for k, v in rows.items()},
           "tier1_ranges": {k: rng(k) for k in
                            ("beta", "lev_median", "lev_p95", "max_dd",
                             "active_frac", "skip_rate")},
           "global_max_dd": gdd, "dollar_tilt_worst": worst,
           "funding_share_of_net": res.total_funding / net,
           "tier2_pooled": bb["pooled"],
           "note": "calibration from an already-logged config; no trial consumed"}
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=tier_calibration)")


if __name__ == "__main__":
    main()
