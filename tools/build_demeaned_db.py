#!/usr/bin/env python3
"""
Build a per-symbol drift-demeaned copy of the point-in-time store.

  python tools/build_demeaned_db.py --src xsmom.db --dst xsmom_demeaned.db

DIAGNOSTIC ONLY — NOT RUNNABLE LIVE
-----------------------------------
Each symbol is demeaned by its FULL-SAMPLE mean log return, which is not
knowable at any point inside the sample. The output exists solely to
attribute an existing result (Sharpe_real − Sharpe_demeaned ≈ the part of
the edge that is drift-harvesting rather than trend-continuation). Results
from it go to diagnostics.jsonl, never trials.jsonl.

WHY A SEPARATE DATABASE, NOT AN ENGINE HOOK
-------------------------------------------
A transform hook inside the engine can be accidentally left enabled and is
invisible in the output. A separate file cannot be: the unmodified engine
runs against it exactly as against the real store, and the file name says
what it is.

WHAT CHANGES
------------
For symbol s with bars indexed t = 0..N−1 in time order, every price column
(open, high, low, close) of bar t is multiplied by exp(−μ_s · t), where μ_s
is the mean daily log close-to-close return. The mean log return becomes 0
exactly; every intrabar ratio (close/open, high/low) is preserved, and each
overnight (close→next open) log return is shifted by the same −μ_s as the
close-to-close return.

Scaling the whole bar — not only `close` as the amendment literally says —
is necessary: the engine fills at the OPEN. Leaving the open unscaled against
a scaled close would inject a synthetic overnight jump of +μ_s·t every day,
growing without bound, and reintroduce the drift through the fills.

WHAT DOES NOT CHANGE
--------------------
volume, quote_volume, trades, open_time, close_time, funding, symbol_filters,
ingest_log. Universe membership depends only on these, so it is byte-identical
between the two stores. Test 13 asserts this.

This is a dataset-construction tool, in the same class as build.py. It is
never imported by backtest/, which touches the database only through PITView.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np


def build_demeaned_db(
    src: str | Path, dst: str | Path, interval: str = "1d"
) -> dict[str, float]:
    """
    Copy `src` to `dst` and demean per-symbol log returns in place.
    Returns {symbol: mu} (the removed daily log drift). Refuses to overwrite.
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        raise FileExistsError(
            f"{dst} exists; delete it explicitly rather than silently rebuilding"
        )

    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst)
    try:
        # Consistent snapshot even if the source is in WAL mode / in use.
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()

    mus: dict[str, float] = {}
    try:
        symbols = [
            r[0]
            for r in dst_conn.execute(
                "SELECT DISTINCT symbol FROM klines WHERE interval = ? "
                "ORDER BY symbol",
                (interval,),
            )
        ]
        for sym in symbols:
            rows = dst_conn.execute(
                "SELECT open_time, open, high, low, close FROM klines "
                "WHERE symbol = ? AND interval = ? ORDER BY open_time",
                (sym, interval),
            ).fetchall()
            if len(rows) < 2:
                mus[sym] = 0.0  # nothing to demean; single bar left as is
                continue
            closes = np.array([r[4] for r in rows], dtype=float)
            if np.any(closes <= 0):
                raise ValueError(f"{sym}: non-positive close; refusing to guess")
            mu = float(np.diff(np.log(closes)).mean())
            mus[sym] = mu
            factors = np.exp(-mu * np.arange(len(rows), dtype=float))
            dst_conn.executemany(
                "UPDATE klines SET open = ?, high = ?, low = ?, close = ? "
                "WHERE symbol = ? AND interval = ? AND open_time = ?",
                [
                    (r[1] * f, r[2] * f, r[3] * f, r[4] * f, sym, interval, r[0])
                    for r, f in zip(rows, factors)
                ],
            )
            # The execution (1m) bars of day i must carry the SAME factor as
            # day i's daily bar. Scaling the daily bars alone would leave the
            # fills on an undemeaned price series -- and, since the engine
            # fills at the +1min open, would put the drift straight back in
            # through every fill while looking correct in the daily data.
            by_day = {r[0]: f for r, f in zip(rows, factors)}
            mrows = dst_conn.execute(
                "SELECT open_time, open, high, low, close FROM klines "
                "WHERE symbol = ? AND interval = '1m' ORDER BY open_time",
                (sym,),
            ).fetchall()
            if mrows:
                DAY_MS = 86_400_000
                upd = []
                for m in mrows:
                    f = by_day.get((m[0] // DAY_MS) * DAY_MS)
                    if f is None:
                        continue      # minute bar with no daily bar: leave it
                    upd.append((m[1] * f, m[2] * f, m[3] * f, m[4] * f,
                                sym, m[0]))
                dst_conn.executemany(
                    "UPDATE klines SET open = ?, high = ?, low = ?, close = ? "
                    "WHERE symbol = ? AND interval = '1m' AND open_time = ?",
                    upd,
                )
        dst_conn.commit()
    finally:
        dst_conn.close()

    meta = {
        "diagnostic_only": True,
        "note": "per-symbol FULL-SAMPLE mean log return removed; "
                "not knowable live; results belong in diagnostics.jsonl",
        "source": str(src),
        "interval": interval,
        "n_symbols": len(mus),
        "mu_daily_log": mus,
    }
    dst.with_suffix(dst.suffix + ".meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    return mus


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--src", default="xsmom.db")
    p.add_argument("--dst", default="xsmom_demeaned.db")
    p.add_argument("--interval", default="1d")
    a = p.parse_args()
    mus = build_demeaned_db(a.src, a.dst, a.interval)
    vals = np.array(list(mus.values()))
    print(f"wrote {a.dst}: {len(mus)} symbols demeaned")
    if len(vals):
        print(f"removed daily log drift: mean {vals.mean():+.5f} | "
              f"min {vals.min():+.5f} | max {vals.max():+.5f} | "
              f"annualised mean {vals.mean() * 365:+.2%}")
    print("DIAGNOSTIC ONLY — full-sample means; not runnable live.")


if __name__ == "__main__":
    main()
