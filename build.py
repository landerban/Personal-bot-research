#!/usr/bin/env python3
"""
Build the point-in-time dataset.

  python3 build.py filters                 # snapshot exchange filters (do this daily)
  python3 build.py symbols                 # list every symbol ever listed
  python3 build.py backfill --limit 50     # ingest klines + funding
  python3 build.py audit                   # coverage + known-unknowns
  python3 build.py tradeable --capital 100 # what you can actually trade

Run `filters` daily from today. Binance publishes only *current* filters, so
every day you skip is a day whose true MIN_NOTIONAL is unrecoverable.
"""

import argparse
import json
import time
from datetime import date

from pitdata.store import PointInTimeStore
from pitdata import download

DB = "xsmom.db"


def cmd_symbols(args):
    syms = download.list_all_symbols()
    usdt = [s for s in syms if s.endswith("USDT")]
    print(f"{len(syms)} symbols ever listed; {len(usdt)} USDT-quoted")
    print("(includes delisted -- this is the survivorship-safe list)")
    with open("symbols.json", "w") as f:
        json.dump(usdt, f, indent=2)
    print(f"wrote symbols.json")


def cmd_filters(args):
    store = PointInTimeStore(DB)
    rows = download.fetch_exchange_filters()
    n = store.insert_filters(int(time.time() * 1000), rows)
    mn = sorted(
        {r["min_notional"] for r in rows if r["min_notional"] is not None}
    )
    print(f"snapshotted {n} symbols")
    print(f"distinct MIN_NOTIONAL values observed: {mn}")
    high = [r["symbol"] for r in rows
            if r["min_notional"] and r["min_notional"] > 20]
    if high:
        print(f"\nsymbols with MIN_NOTIONAL > $20 ({len(high)}):")
        print("  " + ", ".join(sorted(high)[:25]))
        print("  ^ these may be untradeable at small capital")
    store.close()


def cmd_backfill(args):
    store = PointInTimeStore(DB)
    with open("symbols.json") as f:
        symbols = json.load(f)
    if args.limit:
        symbols = symbols[: args.limit]
    start = date.fromisoformat(args.start)
    res = download.backfill(store, symbols, start=start, interval=args.interval)
    bad = [r for r in res if not r.ok]
    print(f"\ningested {sum(r.rows for r in res if r.ok):,} rows")
    if bad:
        print(f"{len(bad)} failures; first few:")
        for r in bad[:5]:
            print(f"  {r.symbol} {r.period}: {r.reason}")
    store.close()


def cmd_audit(args):
    store = PointInTimeStore(DB)
    cov = store.coverage(args.interval)
    print(f"symbols with data: {len(cov)}")
    if cov:
        total = sum(c[1] for c in cov)
        print(f"total bars: {total:,}")
        print(f"earliest: {min(c[2] for c in cov)}")
        print(f"latest:   {max(c[3] for c in cov)}")
    print("\nfilter coverage (known-unknowns):")
    for k, v in store.audit_filter_coverage().items():
        print(f"  {k}: {v}")
    store.close()


def cmd_tradeable(args):
    store = PointInTimeStore(DB)
    latest = store._conn.execute("SELECT MAX(close_time) FROM klines").fetchone()[0]
    if latest is None:
        print("no data ingested yet")
        return
    v = store.view_as_of(latest)
    full = v.universe(min_quote_volume=args.min_volume)
    trad = v.tradeable_universe(
        capital=args.capital,
        gross_leverage=args.leverage,
        n_positions=args.n,
        min_quote_volume=args.min_volume,
    )
    avg = args.leverage * args.capital / args.n
    print(f"capital ${args.capital:,.0f} | {args.leverage}x gross | N={args.n}")
    print(f"average position: ${avg:.2f}; smallest after weighting: ${avg*0.25:.2f}")
    print(f"\nliquid universe:    {len(full)}")
    print(f"tradeable at size:  {len(trad)}")
    excluded = sorted(set(full) - set(trad))
    if excluded:
        print(f"excluded by MIN_NOTIONAL ({len(excluded)}): {', '.join(excluded[:20])}")
    if len(trad) < args.n:
        print(f"\nWARNING: only {len(trad)} tradeable but N={args.n} requested.")
        print("Either raise capital or lower N. Do not silently run a smaller book.")
    store.close()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("symbols").set_defaults(fn=cmd_symbols)
    sub.add_parser("filters").set_defaults(fn=cmd_filters)

    b = sub.add_parser("backfill")
    b.add_argument("--start", default="2019-09-01")
    b.add_argument("--interval", default="1d")
    b.add_argument("--limit", type=int, default=None)
    b.set_defaults(fn=cmd_backfill)

    a = sub.add_parser("audit")
    a.add_argument("--interval", default="1d")
    a.set_defaults(fn=cmd_audit)

    t = sub.add_parser("tradeable")
    t.add_argument("--capital", type=float, default=100.0)
    t.add_argument("--leverage", type=float, default=2.0)
    t.add_argument("--n", type=int, default=10)
    t.add_argument("--min-volume", type=float, default=5_000_000.0)
    t.set_defaults(fn=cmd_tradeable)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
