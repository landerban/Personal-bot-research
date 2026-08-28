#!/usr/bin/env python3
"""
Stage 10 2.3 / Stage 11 5: which of the intended top-15 exist on the Binance
futures testnet, and what N does that support?

  python tools/testnet_symbols.py

Testnet lists fewer symbols than production. Paper trades whatever subset
exists, and the gap is recorded as a KNOWN LIMITATION -- stated, never
silently absorbed (NOTES 46.5).

TWO CORRECTIONS SINCE THE FIRST RUN (NOTES 48.0)
------------------------------------------------
1. The presence test used to filter `contractType == "PERPETUAL"`, which
   excludes `TRADIFI_PERPETUAL` -- so tokenised commodity and pre-market perps
   that ARE listed were reported absent. Presence now means "listed and
   TRADING", whatever the contract type.
2. The intended universe is now the CRYPTO-ONLY top-15 (NOTES 48), so the
   TradFi instruments are excluded before ranking rather than counted as
   missing coverage.

Read-only: one unsigned GET to testnet exchangeInfo plus a point-in-time read
of the research store. No keys are needed and none are used.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from backtest.engine import Config  # noqa: E402
from backtest.universe_filter import filter_universe  # noqa: E402
from backtest.weights import LIQUIDITY_RANK_WINDOW, MIN_LEG_NAMES  # noqa: E402
from live.client import TestnetClient  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402


def d(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def ranked(db: Path, cfg: Config, as_of_ms: int):
    """(ranked [(symbol, median volume)], last-bar map) at `as_of_ms`, by the
    same measure weights.compute_target_weights uses. Unfiltered."""
    store = PointInTimeStore(db, read_only=True)
    try:
        view = store.view_as_of(as_of_ms)
        uni = view.tradeable_universe(
            capital=cfg.initial_capital,
            gross_leverage=cfg.max_gross_leverage,
            n_positions=cfg.n_positions,
            min_quote_volume=cfg.min_quote_volume,
        )
        med = []
        for sym in uni:
            bars = view.klines(sym, "1d", limit=LIQUIDITY_RANK_WINDOW)
            if len(bars) < LIQUIDITY_RANK_WINDOW:
                continue
            med.append((median(b.quote_volume for b in bars), sym))
        med.sort(reverse=True)
        last_bar = {}
        for _, sym in med:
            b = view.klines(sym, "1d", limit=1)
            if b:
                last_bar[sym] = b[-1].close_time
        return [(s, v) for v, s in med], last_bar
    finally:
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--as-of", default=None)
    a = ap.parse_args()

    cfg = Config(lookback=14, skip=0, vol_target=0.10, initial_capital=800.0,
                 max_liquidity_rank=15, slippage_bps_per_side=5.0)

    store = PointInTimeStore(a.db, read_only=True)
    end = runner.data_end_ms(store)
    store.close()
    as_of = end if a.as_of is None else (
        int(datetime.fromisoformat(a.as_of).replace(tzinfo=timezone.utc)
            .timestamp() * 1000) + 86_400_000 - 1)

    print(f"=== testnet coverage of the CRYPTO-ONLY top-{cfg.max_liquidity_rank} "
          f"(NOTES 48) ===")
    print(f"universe as of {d(as_of)}\n")

    full, last_bar = ranked(Path(a.db), cfg, as_of)
    # NOTES 48.11: pass recency so a symbol that is still trading but
    # absent from the (testnet) metadata snapshot is excluded as ambiguous
    # rather than admitted by the 48.4 fallback.
    crypto_syms, dropped = filter_universe(
        [s for s, _ in full], last_bar_ms=last_bar, reference_ms=as_of)
    vol = dict(full)
    intended = [(s, vol[s]) for s in crypto_syms][:cfg.max_liquidity_rank]

    excluded_in_top15 = [v for v in dropped
                         if [s for s, _ in full].index(v.symbol) < 15]
    print(f"the crypto filter removed {len(excluded_in_top15)} name(s) from the "
          f"unfiltered top-15:")
    for v in excluded_in_top15:
        print(f"    {v.symbol:<14} {v.reason}")

    c = TestnetClient(api_key="", api_secret="")
    info = c.request("GET", "/fapi/v1/exchangeInfo")
    # presence = listed and TRADING, whatever the contract type (NOTES 48.0)
    listed = {s["symbol"] for s in info.get("symbols", [])
              if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"}

    print(f"\n{'rank':>4}  {'symbol':<14} {'median quote vol (30d)':>24}  testnet")
    present, missing = [], []
    for i, (sym, v) in enumerate(intended, start=1):
        ok = sym in listed
        (present if ok else missing).append(sym)
        print(f"{i:>4}  {sym:<14} {v:>24,.0f}  {'YES' if ok else 'MISSING'}")

    n = len(intended)
    print(f"\ncoverage: {len(present)} of {n} ({len(present) / n:.0%})"
          if n else "\nno intended universe")
    if missing:
        print(f"MISSING on testnet: {', '.join(missing)}")

    # feasible N: even, both legs >= MIN_LEG_NAMES, no more than the names
    # actually available
    avail = len(present)
    feasible_n = min(cfg.n_positions, avail - (avail % 2))
    ok_full = feasible_n >= cfg.n_positions
    print(f"\ntradeable names on testnet: {avail}")
    print(f"frozen config wants N={cfg.n_positions} (k={cfg.n_positions // 2}); "
          f"MIN_LEG_NAMES={MIN_LEG_NAMES} so the floor is N={MIN_LEG_NAMES * 2}")
    if ok_full:
        print(f"  -> FULL frozen config runs on paper: N={cfg.n_positions}. "
              f"The 47.5 reduced-N limitation is RETIRED.")
    elif feasible_n >= MIN_LEG_NAMES * 2:
        print(f"  -> reduced N={feasible_n} (k={feasible_n // 2}). A VENUE "
              f"constraint, not a tuning decision: no paper observation may "
              f"justify a strategy parameter (NOTES 46.7). Limitation STANDS.")
    else:
        print(f"  -> only {avail} names: below the N={MIN_LEG_NAMES * 2} floor. "
              f"Paper cannot run the cross-sectional book at all on testnet.")

    out = {
        "ts": int(time.time()), "kind": "testnet_symbol_coverage",
        "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
        "as_of": d(as_of), "base_url": c.base_url,
        "crypto_only": True,
        "excluded_from_unfiltered_top15": [
            {"symbol": v.symbol, "reason": v.reason} for v in excluded_in_top15],
        "intended": [s for s, _ in intended],
        "present": present, "missing": missing,
        "coverage": len(present) / n if n else None,
        "feasible_n": feasible_n, "full_config_runs": ok_full,
        "note": "read-only; unsigned; NOTES 46.5 known limitation; supersedes "
                "the pre-48.0 run whose presence test filtered contractType",
    }
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} "
          f"(kind=testnet_symbol_coverage)")


if __name__ == "__main__":
    main()
