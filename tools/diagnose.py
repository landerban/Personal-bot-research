#!/usr/bin/env python3
"""
What is actually in xsmom.db?

    python tools/diagnose.py [xsmom.db]

Diagnostics only -- reads the DB directly rather than through PITView. That is
fine here (no strategy decision depends on it) and would NOT be fine in
backtest code.

Stage 2b A2: the smallest-position arithmetic imports MIN_WEIGHT_FRACTION
from pitdata.store -- the single source of truth -- instead of hardcoding
0.5. Test 15 asserts this module's constant IS the store's. The bug this
guards against: two places holding "the same" number and disagreeing
(0.25 vs 0.5 is how "0 tradeable at 1.0x" happened).
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from pitdata.store import MIN_WEIGHT_FRACTION, PointInTimeStore  # noqa: E402

DB = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "xsmom.db"


def ts(ms):
    if ms is None:
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        print(f"cannot open {DB}: {e}")
        return

    print(f"=== {DB} ===\n")

    # ---- klines ----
    row = c.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(close_time), MAX(close_time) "
        "FROM klines WHERE interval='1d'"
    ).fetchone()
    bars, syms, lo, hi = row
    print(f"KLINES   {bars:,} bars / {syms} symbols")
    print(f"         {ts(lo)} -> {ts(hi)}")
    if lo and hi:
        yrs = (hi - lo) / (365.25 * 86_400_000)
        print(f"         span {yrs:.2f} years")
        if yrs < 6.0:
            print("         !! expected ~6.6y from 2020-01 (the monthly futures "
                  "dumps start there, not 2019-09). Backfill may be partial.")
    if syms and syms <= 25:
        print(f"         !! only {syms} symbols -- did you run with --limit?")
        print("            N=10 cross-sectional needs a much wider candidate pool.")

    # ---- funding ----
    row = c.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(funding_time), MAX(funding_time) "
        "FROM funding"
    ).fetchone()
    fn, fsyms, flo, fhi = row
    print(f"\nFUNDING  {fn:,} settlements / {fsyms} symbols")
    print(f"         {ts(flo)} -> {ts(fhi)}")
    if fn == 0:
        print("         !! NO FUNDING DATA. Backtest results would be fiction.")
    elif syms and fsyms < syms:
        print(f"         !! {syms - fsyms} symbols have klines but no funding.")

    # ---- filters: the load-bearing unknown ----
    row = c.execute(
        "SELECT COUNT(DISTINCT symbol), MIN(snapshot_time), MAX(snapshot_time) "
        "FROM symbol_filters"
    ).fetchone()
    nf, slo, shi = row
    print(f"\nFILTERS  {nf} symbols; snapshots {ts(slo)} -> {ts(shi)}")
    if nf == 0:
        print("         !! NEVER SNAPSHOTTED. Run: python build.py filters")
        print("            MIN_NOTIONAL is unknown, so sizing is unverified.")
    else:
        vals = c.execute(
            "SELECT min_notional, COUNT(*) FROM symbol_filters "
            "WHERE min_notional IS NOT NULL GROUP BY min_notional ORDER BY min_notional"
        ).fetchall()
        print("         MIN_NOTIONAL distribution:")
        for v, n in vals:
            print(f"           ${v:>8.2f}  x{n}")

        btc = c.execute(
            "SELECT min_notional FROM symbol_filters WHERE symbol='BTCUSDT' "
            "ORDER BY snapshot_time LIMIT 1"
        ).fetchone()
        print(f"\n         BTCUSDT MIN_NOTIONAL: "
              f"{('$%.2f' % btc[0]) if btc and btc[0] else 'unknown'}")
        if btc and btc[0] and btc[0] > 5:
            print("         ^^ ABOVE $5. BTCUSDT is untradeable at $100.")
            print("            It is still the beta reference (fine -- beta uses")
            print("            its returns, not a position in it).")
    c.close()

    # ---- what is tradeable at $100 ----
    print("\n=== TRADEABLE AT $100 ===")
    if not lo:
        print("no kline data")
        return

    s = PointInTimeStore(DB, read_only=True)
    v = s.view_as_of(hi)
    liquid = v.universe(min_quote_volume=5_000_000)
    print(f"liquid universe (>=$5M median daily): {len(liquid)}")

    capital, n = 100.0, 10
    for L in (1.0, 2.0, 3.0):
        t = v.tradeable_universe(
            capital=capital, gross_leverage=L, n_positions=n,
            min_quote_volume=5_000_000,
        )
        smallest = MIN_WEIGHT_FRACTION * L * capital / n
        flag = "" if len(t) >= n else "   <-- BELOW N=10"
        print(f"  gross {L:.1f}x  smallest position ${smallest:5.2f}  "
              f"tradeable {len(t):3d}{flag}")

    print(f"\nsmallest position = {MIN_WEIGHT_FRACTION} x L x C/N; a $5 floor at "
          f"$100, N=10 needs realised L >= {5.0 * n / (MIN_WEIGHT_FRACTION * capital):.2f}x.")
    print("Realised leverage lands near 1.0x for a beta-neutral book at 20% vol,")
    print("so the 1.0x row is the one that matters, not the 3.0x row.")
    s.close()


if __name__ == "__main__":
    main()
