#!/usr/bin/env python3
"""
Dataset diagnostics: what is in the store, and what is tradeable at size.

  python tools/diagnose.py [--db xsmom.db] [--capital 100] [--n 10]

Prints coverage, funding, filters, the liquid universe, and how many of
those symbols clear MIN_NOTIONAL at several gross-leverage levels. The
smallest-position arithmetic uses pitdata.store.MIN_WEIGHT_FRACTION -- the
single source of truth -- rather than a local constant, so this tool cannot
disagree with the filter it is describing (Stage 2b A2).

Dataset tooling, same class as build.py; never imported by backtest/.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script

from pitdata.store import MIN_WEIGHT_FRACTION, PointInTimeStore  # noqa: E402

DAY_MS = 86_400_000


def _d(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="xsmom.db")
    p.add_argument("--capital", type=float, default=100.0)
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--min-volume", type=float, default=5_000_000.0)
    a = p.parse_args()

    if not Path(a.db).exists():
        raise SystemExit(f"database not found: {a.db}")
    store = PointInTimeStore(a.db, read_only=True)

    cov = store.coverage("1d")
    if not cov:
        raise SystemExit("no klines ingested")
    n_bars = sum(c[1] for c in cov)
    first = min(c[2] for c in cov)
    last = max(c[3] for c in cov)
    years = (last - first) / DAY_MS / 365.0
    print(f"KLINES   {n_bars:,} bars / {len(cov)} symbols   "
          f"{_d(first)} -> {_d(last)} ({years:.2f}y)")

    # Funding totals: aggregate read for a dataset report, read-only
    # connection, not a PITView path (backtest code never does this).
    ro = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    n_f, n_fs = ro.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM funding"
    ).fetchone()
    ro.close()
    print(f"FUNDING  {n_f:,} settlements / {n_fs} symbols")

    audit = store.audit_filter_coverage()
    v = store.view_as_of(last)
    btc_mn = v.min_notional("BTCUSDT")
    print(f"FILTERS  {audit['symbols_with_filters']} symbols; BTCUSDT "
          f"MIN_NOTIONAL = "
          + (f"${btc_mn:.2f}" if btc_mn is not None else "n/a")
          + f"; earliest snapshot {_d(audit['earliest_filter_snapshot'])}")

    liquid = v.universe(a.min_volume)
    print(f"liquid universe (>=${a.min_volume / 1e6:.0f}M median daily): "
          f"{len(liquid)}")
    for L in (1.0, 2.0, 3.0):
        trad = v.tradeable_universe(
            capital=a.capital, gross_leverage=L, n_positions=a.n,
            min_quote_volume=a.min_volume,
        )
        smallest = MIN_WEIGHT_FRACTION * L * a.capital / a.n
        flag = "   <-- BELOW N=%d" % a.n if len(trad) < a.n else ""
        print(f"  gross {L:.1f}x  tradeable {len(trad):>4}   "
              f"(smallest position {MIN_WEIGHT_FRACTION:.2f} x {L:.1f} x "
              f"{a.capital:.0f}/{a.n} = ${smallest:.2f}){flag}")
    print("smallest-position floor is L >= "
          f"{5.0 * a.n / (MIN_WEIGHT_FRACTION * a.capital):.2f}x "
          f"for a $5 MIN_NOTIONAL at ${a.capital:.0f}, N={a.n} "
          "(realised leverage, not the cap)")
    store.close()


if __name__ == "__main__":
    main()
