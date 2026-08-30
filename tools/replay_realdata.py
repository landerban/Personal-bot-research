#!/usr/bin/env python3
"""
Stage 16 Part D.2: does the book form on REAL market data?

  python tools/replay_realdata.py [--days 12]

The same quantity measured for the third and final time (§53.1, §55.8, here).
If formation is again ~0% this **stops**: that would falsify the §55.9
diagnosis rather than confirm another layer is needed, and it needs eyes.

Read-only production market data through `live.proddata` -- no credential, no
signing path, GET-only. No orders anywhere. No trial.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backtest import runner  # noqa: E402
from backtest import weights as W  # noqa: E402
from backtest.universe_filter import filter_universe  # noqa: E402
from live.phase2 import Phase2Config, production_volumes  # noqa: E402
from live.proddata import ProdDataClient  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

DAY = 86_400_000
BARS = 120          # comfortably past the 60-day beta/vol window


def d(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d")


def build_store(c: ProdDataClient, symbols: list[str],
                volumes: dict[str, float]) -> PointInTimeStore:
    """A PIT store from REAL bars, with production liquidity on every row.

    Same construction as the live feed (NOTES 55.1): the `max_liquidity_rank`
    cap reads `quote_volume` from this store, so it must carry the real
    measure or the cap silently re-ranks on something else.
    """
    store = PointInTimeStore(":memory:")
    for sym in symbols:
        rows = c.klines(sym, "1d", limit=BARS)
        if not rows:
            continue
        ref = float(volumes.get(sym, 0.0))
        store.insert_klines(sym, "1d", [
            (int(r[0]), int(r[6]), float(r[1]), float(r[2]), float(r[3]),
             float(r[4]), float(r[5]), ref, int(r[8])) for r in rows])
        fr = c.funding_rates(sym, start_ms=int(time.time() * 1000) - 60 * DAY)
        if fr:
            store.insert_funding(sym, [(int(x["fundingTime"]),
                                        float(x["fundingRate"])) for x in fr])
    info = c.exchange_info()
    store.insert_filters(int(time.time() * 1000), [{
        "symbol": s["symbol"], "status": s.get("status", "UNKNOWN"),
        "min_notional": next((float(f["notional"]) for f in s.get("filters", [])
                              if f.get("filterType") == "MIN_NOTIONAL"), None),
        "step_size": None, "tick_size": None} for s in info.get("symbols", [])])
    return store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=12)
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    cfg = Phase2Config()
    bcfg = cfg.to_backtest_config()
    c = ProdDataClient()

    print("=== Stage 16 D.2: does the book form on REAL data? ===")
    print("    read-only production market data; no orders; no trial")
    print(f"    config: lb{bcfg.lookback} skip{bcfg.skip} N={bcfg.n_positions} "
          f"vol {bcfg.vol_target:.0%} ${bcfg.initial_capital:.0f} "
          f"top-{bcfg.max_liquidity_rank}\n")

    volumes, age = production_volumes(a.db)
    info = c.exchange_info()
    listed = [s["symbol"] for s in info.get("symbols", [])
              if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"]
    ranked = sorted([s for s in listed if s in volumes],
                    key=lambda s: -volumes[s])
    shortlist = filter_universe(ranked)[0][:cfg.shortlist]
    if W.BTC not in shortlist:
        shortlist.append(W.BTC)
    print(f"shortlist ({len(shortlist)}) by production volume, crypto-only:")
    print("  " + ", ".join(shortlist[:16]))
    print(f"  volume reference {age:.0f} days old\n")

    print(f"fetching {len(shortlist)} symbols x {BARS} daily bars ...",
          flush=True)
    store = build_store(c, shortlist, volumes)

    store.reset_clock()
    probe = store.view_as_of(int(time.time() * 1000))
    last = probe.klines(W.BTC, "1d", limit=2)[-1].close_time

    print(f"\n=== REPLAY: last {a.days} days on REAL data ===")
    out = Counter()
    books = 0
    rows = []
    for k in range(a.days - 1, -1, -1):
        store.reset_clock()
        v = store.view_as_of(last - k * DAY)
        dec = W.compute_target_weights(v, bcfg, cfg.capital)
        if isinstance(dec, W.Skip):
            out[dec.reason] += 1
            rows.append({"date": d(last - k * DAY), "book": False,
                         "reason": dec.reason, "detail": dec.detail})
            print(f"  {d(last - k * DAY)}  SKIP  {dec.reason:<30} "
                  f"{dec.detail[:40]}")
        else:
            books += 1
            out["BOOK"] += 1
            rows.append({"date": d(last - k * DAY), "book": True,
                         "names": len(dec.final_weights), "gross": dec.gross,
                         "est_vol": dec.est_vol_ann,
                         "min_pos": dec.min_position_notional,
                         "dropped": list(dec.dropped)})
            print(f"  {d(last - k * DAY)}  BOOK  {len(dec.final_weights)} names "
                  f"gross {dec.gross:.3f} estvol {dec.est_vol_ann:.1%} "
                  f"minpos ${dec.min_position_notional:.2f}"
                  + (f" dropped {list(dec.dropped)}" if dec.dropped else ""))

    rate = books / a.days
    print(f"\n  BOOK FORMATION: {books} of {a.days} ({rate:.0%})")
    print(f"    testnet, synthetic ranking (53.1)  0 of 12   (0%)")
    print(f"    testnet, production ranking (55.8) 0 of 12   (0%)")
    print(f"    production data (here)             {books} of {a.days}   ({rate:.0%})")
    for k_, v_ in out.most_common():
        print(f"    {k_:<32}{v_:>4}")

    # beta identifiability on the traded universe -- the §55.9 mechanism
    store.reset_clock()
    v = store.view_as_of(last)
    bt = W._aligned_closes(v, W.BTC, bcfg.beta_window + 1)
    ident = None
    if bt is not None:
        btr = np.diff(bt[1]) / bt[1][:-1]
        uni = filter_universe(v.tradeable_universe(
            capital=bcfg.initial_capital, gross_leverage=bcfg.max_gross_leverage,
            n_positions=bcfg.n_positions,
            min_quote_volume=bcfg.min_quote_volume))[0]
        from statistics import median
        med = []
        for s in uni:
            b = v.klines(s, "1d", limit=W.LIQUIDITY_RANK_WINDOW)
            if len(b) >= W.LIQUIDITY_RANK_WINDOW:
                med.append((median(x.quote_volume for x in b), s))
        med.sort(reverse=True)
        capped = sorted(s for _, s in med[:bcfg.max_liquidity_rank])
        bad = 0
        print(f"\n=== beta identifiability on REAL data (traded top-"
              f"{bcfg.max_liquidity_rank}) ===")
        print(f"{'symbol':<14} {'beta':>8} {'SE':>8}")
        for s in capped:
            got = W._aligned_closes(v, s, bcfg.beta_window + 1)
            if got is None or got[0] != bt[0]:
                continue
            rr = np.diff(got[1]) / got[1][:-1]
            b = float(W.compute_betas(rr.reshape(-1, 1), btr)[0])
            se = float(W.compute_beta_ses(rr.reshape(-1, 1), btr,
                                          np.array([b]))[0])
            bad += 0 if abs(b) > se else 1
            print(f"{s:<14} {b:>8.3f} {se:>8.3f}"
                  + ("" if abs(b) > se else "   NO"))
        ident = {"capped": capped, "unidentified": bad, "n": len(capped)}
        print(f"  unidentified: {bad} of {len(capped)}   "
              f"(testnet was 2 of 15 after the 55.1 fix)")
    store.close()

    print(f"\n=== VERDICT (NOTES 56.5) ===")
    if books == 0:
        verdict = ("STOP: formation is still 0% on REAL data. That FALSIFIES "
                   "the 55.9 diagnosis -- the blocker is not the sandbox data "
                   "-- and needs eyes, not another layer.")
    else:
        verdict = (f"The book forms on real data: {books} of {a.days} "
                   f"({rate:.0%}). The 55.9 diagnosis is confirmed: the "
                   f"blocker was testnet's synthetic history, not the code "
                   f"and not the guard.")
    print(f"  {verdict}")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "replay_realdata",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "days": a.days, "books": books, "rate": rate,
            "reasons": dict(out), "rows": rows, "identifiability": ident,
            "shortlist": shortlist, "volume_ref_age_days": age,
            "verdict": verdict,
            "note": "read-only production data; no orders; no trial",
        }, default=str) + "\n")
    print(f"  logged to {runner.DIAGNOSTICS_PATH.name} (kind=replay_realdata)")
    sys.exit(0 if books else 1)


if __name__ == "__main__":
    main()
