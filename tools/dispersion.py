#!/usr/bin/env python3
"""
Stage 3a 2: cross-sectional dispersion and BTC correlation, per year.

  python tools/dispersion.py

Answers: is the per-year price-PnL decline (+163 -> +110 -> +30 -> -37)
REGIME (momentum needs cross-sectional dispersion; correlated markets have
nothing to rank) or DECAY? The reading was fixed in advance in NOTES 22 and
committed before this was run.

Everything is read through PITView. This is a diagnostic, but computing
dispersion from ungated data would use future information, and the habit
matters more than the one number.

Consumes NO trial budget: it evaluates no strategy and selects nothing.
Writes diagnostics.jsonl.
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

from backtest import runner  # noqa: E402
from backtest.engine import Config  # noqa: E402
from backtest.weights import BTC, _aligned_closes  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

DAY = 86_400_000


def stamp(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--lookback", type=int, default=14, help="frozen lookback")
    ap.add_argument("--corr-every", type=int, default=7,
                    help="days between correlation samples (it needs 61 closes "
                         "per symbol, so it is sampled rather than daily)")
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, skip=0)
    start, end = runner.split_view_range("train")
    store = PointInTimeStore(a.db, read_only=True)

    per_day = []
    i = 0
    for view in store.iter_views(start, end, DAY):
        i += 1
        uni = view.tradeable_universe(
            capital=cfg.initial_capital, gross_leverage=cfg.max_gross_leverage,
            n_positions=cfg.n_positions, min_quote_volume=cfg.min_quote_volume)
        rets = []
        for sym in uni:
            r = view.trailing_return(sym, lookback=cfg.lookback, skip=cfg.skip)
            if r is not None:
                rets.append(r)
        if len(rets) < cfg.n_positions:
            continue
        arr = np.sort(np.array(rets))
        k = max(1, len(arr) // 10)
        row = {
            "ts": view.as_of,
            "n": len(arr),
            "sd": float(arr.std(ddof=1)),
            "decile_spread": float(arr[-k:].mean() - arr[:k].mean()),
            "corr": None,
        }
        # Correlation is 61 closes per symbol, so sample it.
        if i % a.corr_every == 0:
            btc = _aligned_closes(view, BTC, cfg.beta_window + 1)
            if btc is not None:
                bt, bc = btc
                br = np.diff(bc) / bc[:-1]
                cs = []
                for sym in uni:
                    if sym == BTC:
                        continue
                    got = _aligned_closes(view, sym, cfg.beta_window + 1)
                    if got is None or got[0] != bt or np.any(got[1] <= 0):
                        continue
                    sr = np.diff(got[1]) / got[1][:-1]
                    if sr.std() > 0 and br.std() > 0:
                        cs.append(float(np.corrcoef(sr, br)[0, 1]))
                if cs:
                    row["corr"] = float(np.mean(cs))
        per_day.append(row)
    store.close()

    by_year = defaultdict(list)
    for r in per_day:
        by_year[stamp(r["ts"]).year].append(r)

    print(f"=== Stage 3a 2: cross-sectional dispersion | {cfg.lookback}d trailing "
          f"returns | PIT universe | train ===")
    print(f"{'year':>5} {'days':>5} {'universe':>9} | {'sd mean':>8} {'sd med':>8} "
          f"| {'decile mean':>12} {'decile med':>11} | {'corr to BTC':>12}")
    out = {}
    for y in sorted(by_year):
        rows = by_year[y]
        sd = np.array([r["sd"] for r in rows])
        ds = np.array([r["decile_spread"] for r in rows])
        cr = np.array([r["corr"] for r in rows if r["corr"] is not None])
        n = np.array([r["n"] for r in rows])
        out[y] = {
            "days": len(rows), "universe_mean": float(n.mean()),
            "sd_mean": float(sd.mean()), "sd_median": float(np.median(sd)),
            "decile_mean": float(ds.mean()), "decile_median": float(np.median(ds)),
            "corr_mean": float(cr.mean()) if len(cr) else float("nan"),
            "corr_samples": int(len(cr)),
        }
        o = out[y]
        print(f"{y:>5} {o['days']:>5} {o['universe_mean']:>9.0f} | "
              f"{o['sd_mean']:>8.2%} {o['sd_median']:>8.2%} | "
              f"{o['decile_mean']:>12.2%} {o['decile_median']:>11.2%} | "
              f"{o['corr_mean']:>12.3f}")

    # crash months vs their annual mean
    print("\ncrash months vs the 2022 annual mean:")
    m2022 = out.get(2022, {})
    for label, (yy, mm) in (("2022-05 LUNA", (2022, 5)), ("2022-11 FTX", (2022, 11))):
        rows = [r for r in per_day
                if stamp(r["ts"]).year == yy and stamp(r["ts"]).month == mm]
        if not rows:
            continue
        sd = np.mean([r["sd"] for r in rows])
        ds = np.mean([r["decile_spread"] for r in rows])
        print(f"  {label:<14} sd {sd:>7.2%} (yr {m2022.get('sd_mean', float('nan')):.2%})"
              f"   decile {ds:>7.2%} (yr {m2022.get('decile_mean', float('nan')):.2%})")

    ys = sorted(out)
    sds = [out[y]["sd_mean"] for y in ys]
    dss = [out[y]["decile_mean"] for y in ys]
    print(f"\nsd by year     : {' -> '.join(f'{v:.2%}' for v in sds)}")
    print(f"decile by year : {' -> '.join(f'{v:.2%}' for v in dss)}")
    print(f"corr by year   : {' -> '.join(f'{out[y]['corr_mean']:.3f}' for y in ys)}")
    print("\nReading (NOTES 22, fixed in advance):")
    print("  collapses 2022 AND stays low 2023 -> regime, momentum dormant")
    print("  collapses 2022 but RECOVERS 2023  -> regime explains 2022 only; decay leads")
    print("  roughly FLAT across all years     -> regime explains nothing; decay")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "dispersion",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "lookback": cfg.lookback, "split": "train",
            "years": {str(k): v for k, v in out.items()},
            "note": "diagnostic through PITView; no strategy evaluated, no trial",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=dispersion)")


if __name__ == "__main__":
    main()
