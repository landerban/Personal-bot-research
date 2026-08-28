#!/usr/bin/env python3
"""
Stage 4 1: the decisive check -- is USDC funding comparable to USDT?

  python tools/usdc_funding_check.py

Funding is 59.7% of net PnL and is 81% long-leg and tail-driven: the income
comes from the ~27% of settlements with violently NEGATIVE rates (crowded
shorts paying longs). That crowding lives where open interest and retail
leverage are, which is USDT. If USDC's negative tail is thinner, switching
margin asset trades ~60% of PnL for a fee saving.

The reading was fixed in NOTES 31 and committed before this ran.

Fetches USDC FUNDING ONLY, into a SEPARATE database, so the frozen
xsmom.db is untouched. Does NOT fetch USDC klines -- that would be backfill
for a strategy that has not been approved (STAGE4 7/9).

Comparison is on matched base assets over their COMMON window only. Using
USDC's short window against USDT's full history would confound the margin
switch with the regime.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pitdata.download import fetch_funding_month, month_range  # noqa: E402

NEG_THRESHOLD = -0.0001          # -0.01% per settlement: the tail that pays
FAPI = "https://fapi.binance.com"


def d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def tail_stats(rates: np.ndarray) -> dict:
    return {
        "n": int(len(rates)),
        "mean": float(rates.mean()),
        "p05": float(np.percentile(rates, 5)),
        "p01": float(np.percentile(rates, 1)),
        "frac_below": float((rates < NEG_THRESHOLD).mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--usdc-db", default=str(ROOT / "usdc_funding.db"))
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    info = requests.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=60).json()
    perp = [s for s in info["symbols"]
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"]
    usdc = {s["symbol"]: s["baseAsset"] for s in perp if s["quoteAsset"] == "USDC"}
    usdt_by_base = {s["baseAsset"]: s["symbol"] for s in perp if s["quoteAsset"] == "USDT"}
    pairs = [(sym, base, usdt_by_base[base]) for sym, base in usdc.items()
             if base in usdt_by_base]
    print(f"USDC perpetuals {len(usdc)} | with a USDT counterpart {len(pairs)}")

    # ---- fetch USDC funding into its own database ------------------------
    con = sqlite3.connect(a.usdc_db)
    con.execute("CREATE TABLE IF NOT EXISTS funding (symbol TEXT, funding_time INTEGER,"
                " funding_rate REAL, PRIMARY KEY (symbol, funding_time))")
    con.commit()
    have = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM funding")}
    todo = [(s, p) for s, _, _ in pairs if s not in have
            for p in month_range(date(2023, 1, 1), date.today())]
    if todo:
        print(f"fetching {len(todo):,} symbol-months of USDC funding "
              f"({a.workers} workers)...", flush=True)
        sess = requests.Session()
        n_rows = 0
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futs = {pool.submit(fetch_funding_month, sess, s, p): (s, p)
                    for s, p in todo}
            for i, f in enumerate(as_completed(futs), 1):
                (sym, period) = futs[f]
                try:
                    rows = f.result()
                except Exception:
                    rows = []
                if rows:
                    con.executemany(
                        "INSERT OR IGNORE INTO funding VALUES (?,?,?)",
                        [(sym, int(t), float(r)) for t, r in rows])
                    n_rows += len(rows)
                if i % 200 == 0:
                    con.commit()
                    print(f"  [{i}/{len(todo)}] {n_rows:,} rows", flush=True)
        con.commit()
        print(f"fetched {n_rows:,} USDC funding rows into {Path(a.usdc_db).name}")

    usdc_f = {}
    for sym, _, _ in pairs:
        rows = con.execute("SELECT funding_time, funding_rate FROM funding "
                           "WHERE symbol=? ORDER BY funding_time", (sym,)).fetchall()
        if rows:
            usdc_f[sym] = (np.array([r[0] for r in rows]),
                           np.array([r[1] for r in rows]))
    con.close()

    src = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    # ---- compare on the COMMON window per base asset ---------------------
    print(f"\n=== Stage 4 1: USDC vs USDT funding, matched base assets, "
          f"COMMON window only ===")
    print(f"{'base':<10} {'common from':>12} {'days':>6} {'n':>6} | "
          f"{'USDT mean':>10} {'p05':>9} {'p01':>9} {'<-0.01%':>8} | "
          f"{'USDC mean':>10} {'p05':>9} {'p01':>9} {'<-0.01%':>8} | {'corr':>6}")
    out = {}
    for sym, base, usym in sorted(pairs):
        if sym not in usdc_f:
            continue
        ct, cr = usdc_f[sym]
        rows = src.execute("SELECT funding_time, funding_rate FROM funding "
                           "WHERE symbol=? ORDER BY funding_time", (usym,)).fetchall()
        if not rows:
            continue
        ut = np.array([r[0] for r in rows]); ur = np.array([r[1] for r in rows])
        lo = max(ct.min(), ut.min()); hi = min(ct.max(), ut.max())
        if hi <= lo:
            continue
        # align on shared settlement timestamps (tolerate ms jitter by snapping)
        SNAP = 3_600_000
        cmap = {(t // SNAP) * SNAP: r for t, r in zip(ct, cr) if lo <= t <= hi}
        umap = {(t // SNAP) * SNAP: r for t, r in zip(ut, ur) if lo <= t <= hi}
        keys = sorted(set(cmap) & set(umap))
        if len(keys) < 100:
            continue
        c = np.array([cmap[k] for k in keys]); u = np.array([umap[k] for k in keys])
        cs, us = tail_stats(c), tail_stats(u)
        corr = float(np.corrcoef(c, u)[0, 1]) if c.std() > 0 and u.std() > 0 else float("nan")
        days = (hi - lo) / 86_400_000
        out[base] = {"usdc_symbol": sym, "usdt_symbol": usym,
                     "common_from": d(lo), "common_days": days,
                     "n_matched": len(keys), "usdt": us, "usdc": cs, "corr": corr}
        print(f"{base:<10} {d(lo):>12} {days:>6.0f} {len(keys):>6} | "
              f"{us['mean']:>+10.6f} {us['p05']:>+9.5f} {us['p01']:>+9.5f} "
              f"{us['frac_below']:>7.1%} | "
              f"{cs['mean']:>+10.6f} {cs['p05']:>+9.5f} {cs['p01']:>+9.5f} "
              f"{cs['frac_below']:>7.1%} | {corr:>6.2f}")
    src.close()

    if not out:
        sys.exit("no matched pairs with sufficient common history")

    # ---- pooled verdict --------------------------------------------------
    fu = np.array([v["usdt"]["frac_below"] for v in out.values()])
    fc = np.array([v["usdc"]["frac_below"] for v in out.values()])
    med_days = float(np.median([v["common_days"] for v in out.values()]))
    ratio = float(fc.mean() / fu.mean()) if fu.mean() > 0 else float("nan")
    print(f"\npooled over {len(out)} matched base assets:")
    print(f"  fraction of settlements below {NEG_THRESHOLD:+.4f} "
          f"(the tail that carries the PnL):")
    print(f"    USDT mean {fu.mean():.2%} | USDC mean {fc.mean():.2%} "
          f"-> USDC/USDT = {ratio:.2f}")
    print(f"  assets where USDC tail is thinner: "
          f"{int((fc < fu).sum())} of {len(out)}")
    print(f"  median common history: {med_days:.0f} days "
          f"({med_days/365:.2f} years)")

    print(f"\n=== BRANCH (NOTES 31, fixed in advance) ===")
    if med_days < 2 * 365:
        branch = (f"THREE: common history is {med_days/365:.2f} years, under the "
                  f"~2-year bar -> NOT ANSWERABLE on available data. Do not "
                  f"extrapolate from USDT")
    elif ratio >= 0.75:
        branch = (f"ONE: USDC negative tail is within ~25% of USDT "
                  f"(ratio {ratio:.2f}) -> funding survives the switch")
    else:
        branch = (f"TWO: USDC negative tail is materially thinner "
                  f"(ratio {ratio:.2f}) -> the switch trades ~60% of PnL for a "
                  f"fee saving. Hypothesis likely dead in this form")
    print(f"  --> BRANCH {branch}")

    rec = {"ts": int(time.time()), "kind": "usdc_funding_check",
           "neg_threshold": NEG_THRESHOLD, "n_pairs": len(out),
           "pooled_frac_below_usdt": float(fu.mean()),
           "pooled_frac_below_usdc": float(fc.mean()),
           "ratio_usdc_over_usdt": ratio,
           "median_common_days": med_days, "branch": branch,
           "per_asset": out,
           "note": "data query only; no backtest, no trial, no USDC klines"}
    with open(ROOT / "diagnostics.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"\nlogged to diagnostics.jsonl (kind=usdc_funding_check)")


if __name__ == "__main__":
    main()
