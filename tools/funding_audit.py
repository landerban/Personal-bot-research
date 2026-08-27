#!/usr/bin/env python3
"""
Stage 3 1.1: exposure-weighted missing-funding audit of the frozen config.

  python tools/funding_audit.py [--lookback 14] [--skip 0] [--slippage-bps 5]

Funding generates most of this strategy's net PnL, and ~17.6% of the
settlements on held positions have no rate in the dataset. A raw COUNT
cannot distinguish 7,300 negligible exposures from 200 large ones on big
shorts, so this weights the gap by the notional actually exposed to it:

    missing_exposure_ratio = sum |notional| over missing settlements
                           / sum |notional| over all expected settlements

Broken out by leg, by year and by symbol. Replays an already-logged trial,
so it is attribution of a completed backtest and consumes NO trial budget.
Results go to diagnostics.jsonl, never trials.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402


def year_of(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--split", default="train")
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, skip=a.skip,
                 slippage_bps_per_side=a.slippage_bps)
    h = runner.config_hash(cfg)
    logged = [t for t in runner.load_trials()
              if t["config_hash"] == h and t["split"] == a.split and not t.get("void")]
    if not logged:
        sys.exit(f"config {h} is not a logged non-void {a.split} trial; this "
                 f"audit replays existing trials only")

    start, end = runner.split_view_range(a.split)
    store = PointInTimeStore(a.db, read_only=True)
    res = run_backtest(store, cfg, start, end)
    store.close()

    exp = res.funding_notional_expected
    miss = res.funding_notional_missing
    ratio = miss / exp if exp > 0 else float("nan")

    print(f"=== Stage 3 1.1 funding audit | lb{cfg.lookback}/skip{cfg.skip} "
          f"@ {cfg.slippage_bps_per_side:.1f}bps | {a.split} ===")
    print(f"missing settlements (count)            : {res.missing_funding_settlements:,}")
    print(f"notional-days exposed to funding       : ${exp:,.0f}")
    print(f"...of which had no rate                : ${miss:,.0f}")
    print(f"\nMISSING_EXPOSURE_RATIO                 : {ratio:.2%}")
    band = ("under 2% -> immaterial, 1.2 optional" if ratio < 0.02
            else "2-5% -> fix what is cheap, 1.3 not required" if ratio < 0.05
            else "5-10% -> 1.2 and 1.3 both indicated" if ratio < 0.10
            else "ABOVE 10% -> the grid result is provisional until fixed")
    print(f"  -> {band}")

    by_leg = defaultdict(float)
    by_year = defaultdict(lambda: [0.0, 0.0, 0])
    by_sym = defaultdict(lambda: [0.0, 0])
    for ts, sym, units, notional, n_miss in res.missing_funding_rows:
        w = notional * n_miss
        by_leg["long" if units > 0 else "short"] += w
        by_year[year_of(ts)][0] += w
        by_year[year_of(ts)][2] += n_miss
        by_sym[sym][0] += w
        by_sym[sym][1] += n_miss

    print("\nby leg (share of all missing exposure):")
    tot_leg = sum(by_leg.values()) or 1.0
    for leg in ("long", "short"):
        print(f"  {leg:<6} ${by_leg[leg]:>14,.0f}  {by_leg[leg]/tot_leg:>6.1%}")

    print("\nby year:")
    print(f"  {'year':>6} {'missing $ notional':>20} {'count':>8}")
    for y in sorted(by_year):
        v = by_year[y]
        print(f"  {y:>6} {v[0]:>20,.0f} {v[2]:>8,}")

    print("\ntop 20 symbols by missing exposure:")
    print(f"  {'symbol':<14} {'missing $ notional':>20} {'count':>8}")
    for sym, v in sorted(by_sym.items(), key=lambda kv: -kv[1][0])[:20]:
        print(f"  {sym:<14} {v[0]:>20,.0f} {v[1]:>8,}")

    rec = {
        "ts": int(time.time()), "kind": "funding_audit",
        "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
        "config_hash": h, "config": asdict(cfg), "split": a.split,
        "missing_settlements": res.missing_funding_settlements,
        "funding_notional_expected": exp,
        "funding_notional_missing": miss,
        "missing_exposure_ratio": ratio,
        "by_leg": dict(by_leg),
        "by_year": {str(k): {"notional": v[0], "count": v[2]} for k, v in by_year.items()},
        "top_symbols": {k: {"notional": v[0], "count": v[1]}
                        for k, v in sorted(by_sym.items(), key=lambda kv: -kv[1][0])[:20]},
        "note": "attribution of a logged trial; no trial budget consumed",
    }
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} (kind=funding_audit)")


if __name__ == "__main__":
    main()
