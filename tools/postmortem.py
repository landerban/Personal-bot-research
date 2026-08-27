#!/usr/bin/env python3
"""
Postmortem of an ALREADY-LOGGED trial: deterministic replay for attribution.

  python tools/postmortem.py --lookback 7 --skip 2 [--split train] [--top 10]

Refuses any config that is not already in trials.jsonl for the split --
replaying a logged, deterministic run yields no new information and so
consumes no trial budget (same footing as the drift decomposition), whereas
a new config would be a trial. Results go to diagnostics.jsonl, never
trials.jsonl.

Prints what the grid report cannot: the beta-hedge scale s per rebalance,
position concentration, and the worst days with per-symbol attribution.
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

from backtest import runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402


def d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, required=True)
    ap.add_argument("--skip", type=int, required=True)
    ap.add_argument("--fee-mode", default="taker")
    ap.add_argument("--split", default="train", choices=("train",))
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, skip=a.skip, fee_mode=a.fee_mode)
    h = runner.config_hash(cfg)
    logged = [t for t in runner.load_trials() if t["config_hash"] == h and t["split"] == a.split]
    if not logged:
        sys.exit(f"config {h} is not a logged {a.split} trial; a postmortem replays "
                 f"existing trials only (running it would spend budget)")
    print(f"postmortem of logged trial {h} (lookback={cfg.lookback} skip={cfg.skip} "
          f"fee={cfg.fee_mode}) x{len(logged)} in trials.jsonl")

    store = PointInTimeStore(a.db, read_only=True)
    start, end = runner.split_view_range(a.split)
    res = run_backtest(store, cfg, start, end)
    store.close()

    # --- hedge scale and concentration per rebalance ---
    S = np.array([rb.beta_scale for rb in res.rebalances])
    K = np.array([rb.vol_scale for rb in res.rebalances])
    G = np.array([rb.gross for rb in res.rebalances])
    maxw = np.array([max(abs(w) for w in rb.final_weights.values()) for rb in res.rebalances])
    short_share = np.array([
        sum(-w for w in rb.final_weights.values() if w < 0) / rb.gross for rb in res.rebalances
    ])
    est = np.array([rb.est_vol_ann for rb in res.rebalances])

    def q(x):
        return (f"min {x.min():.2f} | p05 {np.percentile(x, 5):.2f} | median {np.median(x):.2f}"
                f" | p95 {np.percentile(x, 95):.2f} | max {x.max():.2f}")

    print(f"\nrebalances: {len(res.rebalances)}  bankrupt: {res.bankrupt}")
    print(f"beta scale s (short leg / long leg)   {q(S)}")
    print(f"   s > 2: {(S > 2).mean() * 100:.0f}%   s > 3: {(S > 3).mean() * 100:.0f}%"
          f"   s > 5: {(S > 5).mean() * 100:.0f}%   s < 0.5: {(S < 0.5).mean() * 100:.0f}%")
    print(f"vol scale k                            {q(K)}")
    print(f"gross (final)                          {q(G)}")
    print(f"max |w| single name (of equity)        {q(maxw)}")
    print(f"short-leg share of gross               {q(short_share)}")
    print(f"ex-ante vol of hedged unit book        {q(est)}")

    # --- realised vs ex-ante: daily returns ---
    ts, eq = runner.metrics.strategy_window(res)
    rets = runner.metrics.daily_returns(eq)
    print(f"realised ann vol {runner.metrics.ann_vol(rets) * 100:.0f}%  "
          f"vs target {cfg.vol_target * 100:.0f}%  (ratio {runner.metrics.ann_vol(rets) / cfg.vol_target:.1f}x)")

    # --- worst days with attribution ---
    eq_by_ts = dict(zip(res.timestamps, res.equity))
    pnl_by_ts = dict(zip(res.timestamps, res.pnl_by_symbol_day))
    prev = dict(zip(res.timestamps[1:], res.equity[:-1]))
    days = []
    for t in res.timestamps[1:]:
        e0 = prev[t]
        if e0 <= 0:
            continue
        days.append(((eq_by_ts[t] - e0) / e0, t))
    days.sort()
    print(f"\nworst {a.top} days (return on prior equity; top symbol contributions in $):")
    worst = []
    for r, t in days[: a.top]:
        contrib = sorted(pnl_by_ts[t].items(), key=lambda kv: kv[1])[:3]
        e0 = prev[t]
        txt = ", ".join(f"{s} {p:+.1f} ({p / e0 * 100:+.0f}%)" for s, p in contrib)
        print(f"  {d(t)}  {r * 100:+7.1f}%  equity {e0:8.2f} -> {eq_by_ts[t]:8.2f}   {txt}")
        worst.append({"date": d(t), "ret": r, "equity_before": e0,
                      "contrib": [(s, p) for s, p in contrib]})

    # --- book at the worst day ---
    if days:
        r, t = days[0]
        rb = max((x for x in res.rebalances if x.ts_fill <= t), key=lambda x: x.ts_fill, default=None)
        if rb:
            print(f"\nbook decided {d(rb.ts_decision)} (filled {d(rb.ts_fill)}): s={rb.beta_scale:.2f} "
                  f"k={rb.vol_scale:.2f} gross={rb.gross:.2f} equity={rb.equity_at_decision:.2f}")
            for s_, w in sorted(rb.final_weights.items(), key=lambda kv: kv[1]):
                print(f"    {s_:14s} w={w:+.3f}  ${w * rb.equity_at_decision:+.2f}")

    rec = {
        "ts": int(time.time()), "kind": "postmortem", "git_commit": runner.git_state()[0],
        "dirty": runner.git_state()[1], "config_hash": h, "config": asdict(cfg), "split": a.split,
        "n_rebalances": len(res.rebalances), "bankrupt": res.bankrupt,
        "beta_scale": {"min": float(S.min()), "median": float(np.median(S)), "p95": float(np.percentile(S, 95)),
                       "max": float(S.max()), "frac_gt3": float((S > 3).mean())},
        "max_weight": {"median": float(np.median(maxw)), "max": float(maxw.max())},
        "realised_ann_vol": float(runner.metrics.ann_vol(rets)),
        "ex_ante_vol_median": float(np.median(est)),
        "worst_days": worst,
        "note": "attribution replay of a logged trial; no new configuration",
    }
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=postmortem)")


if __name__ == "__main__":
    main()
