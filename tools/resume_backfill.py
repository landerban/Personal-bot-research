#!/usr/bin/env python3
"""
Resume the Stage 1 backfill from a given symbol.

  python tools/resume_backfill.py --from SIRENUSDT [--start 2019-09-01]

Why this exists: download.backfill() records a symbol-month in ingest_log
only when the dump had rows, so every pre-listing month is a 404 that gets
re-probed on every resume (~10 s per already-complete symbol, ~2 h for a
600-symbol prefix). Handing backfill() only the remaining slice of
symbols.json avoids that. Nothing in pitdata/ is modified; this calls the
same public backfill() exactly the way build.py does, on the same DB.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # build.py resolves symbols.json and the DB from the repo root

from pitdata import download  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

DB = "xsmom.db"  # same file build.py uses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_sym", required=True,
                    help="first symbol to (re)process; earlier ones are skipped")
    ap.add_argument("--start", default="2019-09-01")
    ap.add_argument("--interval", default="1d")
    a = ap.parse_args()

    with open("symbols.json") as f:
        symbols = json.load(f)
    i = symbols.index(a.from_sym)
    rest = symbols[i:]
    print(f"resuming at {a.from_sym} (#{i + 1}/{len(symbols)}): "
          f"{len(rest)} symbols to process", flush=True)

    store = PointInTimeStore(DB)
    res = download.backfill(store, rest, start=date.fromisoformat(a.start),
                            interval=a.interval)
    bad = [r for r in res if not r.ok]
    print(f"\ningested {sum(r.rows for r in res if r.ok):,} rows")
    if bad:
        print(f"{len(bad)} failures; first few:")
        for r in bad[:5]:
            print(f"  {r.symbol} {r.period}: {r.reason}")
    store.close()


if __name__ == "__main__":
    main()
