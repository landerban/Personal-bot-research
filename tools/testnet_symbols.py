#!/usr/bin/env python3
"""
Stage 10 2.3: which of the intended top-15 PIT majors actually exist on the
Binance futures testnet?

  python tools/testnet_symbols.py

Testnet lists fewer symbols than production. Paper trades whatever subset
exists, and the gap is recorded as a KNOWN LIMITATION -- stated, never
silently absorbed (NOTES 46.5).

Read-only: one unsigned GET to the testnet exchangeInfo endpoint, plus a
point-in-time read of the research store. No keys are needed and none are
used.
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
from backtest.weights import LIQUIDITY_RANK_WINDOW  # noqa: E402
from live.client import TestnetClient  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402


def d(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def intended_universe(db: Path, cfg: Config, as_of_ms: int) -> list[tuple[str, float]]:
    """The top-N PIT majors the live path would pick at `as_of_ms`, by the
    SAME measure weights.compute_target_weights uses: median quote volume over
    the trailing LIQUIDITY_RANK_WINDOW days."""
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
        return [(s, v) for v, s in med[:cfg.max_liquidity_rank]]
    finally:
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--as-of", default=None,
                    help="YYYY-MM-DD for the PIT universe snapshot "
                         "(default: the last day the store covers)")
    a = ap.parse_args()

    cfg = Config(lookback=14, skip=0, vol_target=0.10, initial_capital=800.0,
                 max_liquidity_rank=15, slippage_bps_per_side=5.0)

    store = PointInTimeStore(a.db, read_only=True)
    end = runner.data_end_ms(store)
    store.close()
    as_of = end if a.as_of is None else (
        int(datetime.fromisoformat(a.as_of).replace(tzinfo=timezone.utc)
            .timestamp() * 1000) + 86_400_000 - 1)

    print(f"=== Stage 10 2.3: testnet symbol coverage of the top-"
          f"{cfg.max_liquidity_rank} PIT majors ===")
    print(f"intended universe as of {d(as_of)} (last day the research store "
          f"covers)\n")

    intended = intended_universe(Path(a.db), cfg, as_of)

    # unsigned, read-only; the client asserts the testnet host at construction
    c = TestnetClient(api_key="", api_secret="")
    info = c.request("GET", "/fapi/v1/exchangeInfo")
    live_syms = {
        s["symbol"]: s for s in info.get("symbols", [])
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
    }
    print(f"testnet lists {len(live_syms)} TRADING USDT-margined perpetuals "
          f"(base {c.base_url})\n")

    print(f"{'rank':>4}  {'symbol':<14} {'median quote vol (30d)':>24}  testnet")
    present, missing = [], []
    for i, (sym, vol) in enumerate(intended, start=1):
        ok = sym in live_syms
        (present if ok else missing).append(sym)
        print(f"{i:>4}  {sym:<14} {vol:>24,.0f}  {'YES' if ok else 'MISSING'}")

    n = len(intended)
    print(f"\ncoverage: {len(present)} of {n} "
          f"({len(present) / n:.0%})" if n else "\nno intended universe")
    if missing:
        print(f"MISSING on testnet: {', '.join(missing)}")
        print("\nKNOWN LIMITATION (NOTES 46.5): paper trades the reduced set. A")
        print("universe of fewer names changes which names rank and how")
        print("concentrated the book is, so paper-phase BOOK COMPOSITION is NOT")
        print("the composition the backtest validated. This affects the")
        print("operational test not at all -- it is plumbing being exercised --")
        print("and it is recorded rather than absorbed.")
    else:
        print("\nfull coverage: the paper universe equals the intended universe.")

    # symbols the paper path could trade that the research universe does not
    extra = sorted(set(live_syms) - {s for s, _ in intended})
    print(f"\ntestnet also lists {len(extra)} other USDT perps; the paper path "
          f"ignores them (the universe rule is unchanged).")

    out = {
        "ts": int(time.time()), "kind": "testnet_symbol_coverage",
        "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
        "as_of": d(as_of), "base_url": c.base_url,
        "intended": [s for s, _ in intended],
        "present": present, "missing": missing,
        "coverage": len(present) / n if n else None,
        "n_testnet_usdt_perps": len(live_syms),
        "note": "read-only; unsigned; known limitation per NOTES 46.5",
    }
    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} "
          f"(kind=testnet_symbol_coverage)")


if __name__ == "__main__":
    main()
