#!/usr/bin/env python3
"""
Ingest the first minutes of each UTC day as 1-minute bars (Stage 2e 2).

  python tools/backfill_minutes.py [--workers 8] [--minutes 5]

WHY
---
STAGE2_PROMPT specified fills at day T+1's 00:00 open. That is operationally
impossible: at 00:00:00 the universe still has to be built, hundreds of
assets ranked, betas and covariance computed, quantities quantised and
orders transmitted. Against a MOMENTUM signal the delay is one-signed -- if
the move continues into the first minute you systematically buy higher and
sell lower -- so it is not covered by a symmetric slippage parameter.

The engine therefore fills at the 00:01 open. This tool ingests the bars it
needs.

WHAT IT STORES
--------------
Only minutes 00:00..00:04 of each UTC day, into the EXISTING `klines` table
under `interval='1m'`. Nothing in pitdata/ changes: that table is already
gated on `close_time <= as_of`, which is the same gating `test_lookahead.py`
verifies, so a 00:01 bar (close_time 00:01:59.999) cannot be seen by a
decision taken at the previous close.

A full 1-minute history would be ~1.5 billion rows and is not needed; five
bars a day is ~100k rows per symbol-decade and answers the only question the
backtest asks of them.

COST
----
Binance publishes 1m klines only as whole-month zips (~1.8 MB each), so the
whole month is downloaded and all but five bars a day are discarded. About
20.5k symbol-months, ~37 GB of transfer, ~30 minutes with 8 workers.
Idempotent: `ingest_log` source 'klines1m' records completed symbol-months,
so an interrupted run resumes.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pitdata.download import BASE, month_range  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

MINUTE_MS = 60_000
DAY_MS = 86_400_000


def fetch_month(session: requests.Session, symbol: str, period: str,
                keep_minutes: int) -> list[tuple] | None:
    """The first `keep_minutes` 1m bars of each day in one symbol-month."""
    url = f"{BASE}/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{period}.zip"
    for attempt in range(4):
        try:
            r = session.get(url, timeout=90)
            if r.status_code == 404:
                return []          # symbol not listed that month
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = z.read(z.namelist()[0]).decode()
    except (zipfile.BadZipFile, IndexError):
        return None

    out = []
    for row in csv.reader(io.StringIO(raw)):
        if not row or not row[0].strip() or row[0][0].isalpha():
            continue            # header row in some months
        ot = int(row[0])
        if ot > 1e15:           # microsecond stamps in some 2025+ dumps
            ot //= 1000
        minute = (ot % DAY_MS) // MINUTE_MS
        if minute >= keep_minutes:
            continue
        ct = int(row[6])
        if ct > 1e15:
            ct //= 1000
        out.append((ot, ct, float(row[1]), float(row[2]), float(row[3]),
                    float(row[4]), float(row[5]), float(row[7]), int(row[8])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--minutes", type=int, default=5,
                    help="how many minutes from each day's open to keep")
    ap.add_argument("--symbols", default=str(ROOT / "symbols.json"))
    a = ap.parse_args()

    import json
    with open(a.symbols) as f:
        symbols = json.load(f)

    store = PointInTimeStore(a.db)
    done = {
        (r[0], r[1])
        for r in store._conn.execute(
            "SELECT symbol, period FROM ingest_log WHERE source = 'klines1m'"
        ).fetchall()
    }
    # Only fetch symbol-months that actually have daily data.
    wanted = [
        (r[0], r[1])
        for r in store._conn.execute(
            "SELECT symbol, period FROM ingest_log WHERE source = 'klines'"
        ).fetchall()
        if (r[0], r[1]) not in done
    ]
    wanted.sort()
    print(f"{len(wanted):,} symbol-months to fetch "
          f"({len(done):,} already done), {a.workers} workers", flush=True)

    session_local = requests.Session()
    t0 = time.time()
    n_ok = n_rows = n_fail = 0

    def work(item):
        sym, period = item
        return item, fetch_month(session_local, sym, period, a.minutes)

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(work, it): it for it in wanted}
        for i, fut in enumerate(as_completed(futures), 1):
            (sym, period), rows = fut.result()
            if rows is None:
                n_fail += 1
                continue
            if rows:
                store.insert_klines(sym, "1m", rows)
                n_rows += len(rows)
            store._conn.execute(
                "INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?)",
                ("klines1m", sym, period, len(rows), int(time.time() * 1000)),
            )
            store._conn.commit()
            n_ok += 1
            if i % 200 == 0:
                el = time.time() - t0
                rate = i / el
                print(f"[{i}/{len(wanted)}] {sym} {period} | {n_rows:,} rows | "
                      f"{rate:.1f}/s | eta {(len(wanted) - i) / rate / 60:.0f} min",
                      flush=True)

    print(f"done: {n_ok:,} symbol-months, {n_rows:,} minute bars, "
          f"{n_fail} failures, {(time.time() - t0) / 60:.1f} min")
    store.close()


if __name__ == "__main__":
    main()
