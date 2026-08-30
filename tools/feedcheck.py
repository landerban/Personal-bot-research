#!/usr/bin/env python3
"""
Stage 16 B.2: is the production feed really live, complete, and the same
market the research measured?

  python tools/feedcheck.py

Read-only, unsigned, GET-only -- everything goes through `live.proddata`,
which holds no credential and cannot sign or POST.

Five checks, each answering a question that "it returned some JSON" does not:

  1. bar freshness   -- does the last CLOSED 1m bar land within seconds of the
                        minute, and the 1d bar within a day? A stale or
                        misaligned feed looks identical to a live one until
                        you measure the lag.
  2. funding         -- current rate and next settlement time for majors
  3. quote liveness  -- successive bookTicker calls MOVE. A cached or frozen
                        endpoint returns the same numbers forever.
  4. volume agreement-- live 24h quote volumes vs the store's production
                        medians, same order of magnitude. This is the check
                        that the §55 ranking source and the live feed describe
                        the same market.
  5. composition     -- exchangeInfo symbol-set drift against the committed
                        snapshot: the §51.3 staleness / §48.6 guard input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from live.proddata import PROD_BASE, ProdDataClient  # noqa: E402

MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def utc(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    c = ProdDataClient()
    out: dict = {"base": c.base_url, "checks": {}}
    ok_all = True
    print(f"=== Stage 16 B.2: production feed check ===")
    print(f"    {PROD_BASE}  (read-only, unsigned, GET-only)\n")

    # --- 0. clock -------------------------------------------------------
    t0 = time.time()
    server = c.server_time()
    rtt = (time.time() - t0) * 1000
    skew = server - time.time() * 1000
    print(f"server time   {utc(server)}  skew {skew:+.0f} ms, rtt {rtt:.0f} ms")
    out["checks"]["clock"] = {"skew_ms": skew, "rtt_ms": rtt}

    # --- 1. bar freshness ------------------------------------------------
    print(f"\n--- 1. bar freshness (the last CLOSED bar) ---")
    print(f"{'symbol':<10} {'1m close':<20} {'lag':>8}   {'1d close':<20} {'lag':>8}")
    fresh = {}
    for sym in MAJORS:
        m = c.klines(sym, "1m", limit=2)
        d = c.klines(sym, "1d", limit=2)
        # the last row may be the CURRENTLY FORMING bar; the closed one is [-2]
        m_close, d_close = int(m[-2][6]), int(d[-2][6])
        now_ms = time.time() * 1000
        m_lag, d_lag = (now_ms - m_close) / 1000, (now_ms - d_close) / 1000
        fresh[sym] = {"m1_close": m_close, "m1_lag_s": m_lag,
                      "d1_close": d_close, "d1_lag_s": d_lag}
        print(f"{sym:<10} {utc(m_close):<20} {m_lag:>7.0f}s   "
              f"{utc(d_close):<20} {d_lag / 3600:>6.1f}h")
    worst_m = max(v["m1_lag_s"] for v in fresh.values())
    m_ok = worst_m < 180          # a closed 1m bar should be < ~3 min old
    ok_all &= m_ok
    print(f"  -> 1m bars {'FRESH' if m_ok else 'STALE'} "
          f"(worst lag {worst_m:.0f}s, expect < 180s)")
    out["checks"]["freshness"] = {"per_symbol": fresh, "ok": m_ok}

    # --- 2. funding -------------------------------------------------------
    print(f"\n--- 2. funding ---")
    fund = {}
    for sym in MAJORS:
        pi = c.premium_index(sym)
        rate = float(pi["lastFundingRate"])
        nxt = int(pi["nextFundingTime"])
        fund[sym] = {"rate": rate, "next": nxt}
        print(f"{sym:<10} rate {rate:+.6f} ({rate * 3 * 365:+.1%} ann)  "
              f"next {utc(nxt)}")
    out["checks"]["funding"] = fund

    # --- 3. quote liveness -----------------------------------------------
    print(f"\n--- 3. quote liveness (successive calls must move) ---")
    moved = 0
    for sym in MAJORS:
        a1 = c.book_ticker(sym)
        time.sleep(1.2)
        a2 = c.book_ticker(sym)
        changed = (a1["bidPrice"], a1["askPrice"]) != (a2["bidPrice"], a2["askPrice"])
        spread = (float(a2["askPrice"]) - float(a2["bidPrice"]))
        mid = (float(a2["askPrice"]) + float(a2["bidPrice"])) / 2
        half_bps = (spread / 2 / mid) * 1e4 if mid else float("nan")
        moved += changed
        print(f"{sym:<10} bid {a2['bidPrice']:>12} ask {a2['askPrice']:>12}  "
              f"half-spread {half_bps:>5.2f} bps  {'moved' if changed else 'static'}")
    live_ok = moved > 0
    ok_all &= live_ok
    print(f"  -> {moved}/{len(MAJORS)} moved within ~1.2s: "
          f"{'LIVE' if live_ok else 'FROZEN OR CACHED'}")
    print(f"  (half-spread vs the 5 bps fill assumption -- context only, "
          f"adopts nothing)")
    out["checks"]["liveness"] = {"moved": moved, "ok": live_ok}

    # --- 4. volume agreement with the ranking source ---------------------
    print(f"\n--- 4. live 24h volume vs the store's production medians ---")
    from live.phase2 import production_volumes
    store_vol, age_days = production_volumes(a.db)
    rows = []
    print(f"{'symbol':<12} {'live 24h':>18} {'store median':>18} {'ratio':>7}")
    for sym in MAJORS:
        t = c.ticker_24hr(sym)
        live_v = float(t["quoteVolume"])
        ref = store_vol.get(sym, 0.0)
        ratio = live_v / ref if ref else float("nan")
        rows.append({"symbol": sym, "live": live_v, "store": ref, "ratio": ratio})
        print(f"{sym:<12} {live_v:>18,.0f} {ref:>18,.0f} {ratio:>7.2f}")
    same_oom = all(0.1 <= r["ratio"] <= 10 for r in rows
                   if r["ratio"] == r["ratio"])
    ok_all &= same_oom
    print(f"  -> {'SAME ORDER OF MAGNITUDE' if same_oom else 'DIVERGENT'}; "
          f"store reference is {age_days:.0f} days old")
    out["checks"]["volume_agreement"] = {"rows": rows, "ok": same_oom,
                                         "store_age_days": age_days}

    # --- 5. composition drift --------------------------------------------
    print(f"\n--- 5. exchangeInfo drift vs the committed snapshot ---")
    info = c.exchange_info()
    live_syms = {s["symbol"] for s in info.get("symbols", [])}
    snap_path = ROOT / "data" / "underlying_classes_production.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    snap_syms = set(snap["symbols"])
    added, removed = sorted(live_syms - snap_syms), sorted(snap_syms - live_syms)
    h_live = hashlib.sha256(",".join(sorted(live_syms)).encode()).hexdigest()[:16]
    print(f"  live {len(live_syms)} symbols (hash {h_live})")
    print(f"  snapshot {len(snap_syms)} symbols, dated {snap['snapshot_date']}")
    print(f"  added since snapshot   : {len(added)}"
          + (f"  e.g. {', '.join(added[:8])}" if added else ""))
    print(f"  removed since snapshot : {len(removed)}"
          + (f"  e.g. {', '.join(removed[:8])}" if removed else ""))
    print(f"  -> drift is composition-guard input (NOTES 48.6); a symbol the "
          f"snapshot cannot classify is EXCLUDED, never guessed")
    out["checks"]["composition"] = {
        "live_count": len(live_syms), "snapshot_count": len(snap_syms),
        "added": added[:50], "removed": removed[:50], "live_hash": h_live,
        "snapshot_date": snap["snapshot_date"]}

    print(f"\n=== VERDICT: feed is {'LIVE AND CONSISTENT' if ok_all else 'SUSPECT'} ===")
    print(f"    {c.requests_made} GET requests, zero signed, zero orders")
    out["ok"] = ok_all
    out.update(ts=int(time.time()), kind="feedcheck",
               git_commit=runner.git_state()[0], dirty=runner.git_state()[1],
               note="read-only production market data; no credential used")
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, default=str) + "\n")
    print(f"    logged to {runner.DIAGNOSTICS_PATH.name} (kind=feedcheck)")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
