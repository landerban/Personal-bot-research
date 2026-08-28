#!/usr/bin/env python3
"""
Stage 11 §3 / NOTES 48.5: prove the crypto-only filter is INERT on train+validate.

  python tools/prove_filter_noop.py

Re-runs point-in-time universe selection day by day across 2020-01-01 ->
2024-12-31 and asserts the filtered selection is bit-identical to the
unfiltered one -- the FULL eligible ranking, not just the top-15 slice, so a
change below the cut still counts as a diff.

  zero diffs -> the amendment is proven inert everywhere evidence exists, and
                nothing needs revalidating
  ANY diff   -> STOP, report the day and the symbols, and hand the decision
                back. It would mean a non-crypto instrument was in the
                historical universe after all and the amendment is not free.

Touches NO 2025+ data: the loop stops at 2024-12-31 and the assertion is
checked before exit. Consumes no trial -- this configures nothing and measures
no return.
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
from backtest.universe_filter import (  # noqa: E402
    AMBIGUOUS, classify, filter_universe,
)
from backtest.weights import LIQUIDITY_RANK_WINDOW  # noqa: E402
from pitdata.store import PointInTimeStore  # noqa: E402

DAY_MS = 86_400_000
# The window every logged run was measured on. The proof may not exceed it.
PROOF_START = "2020-01-01"
PROOF_END = "2024-12-31"
# NOTES 47.1: the earliest non-crypto listing. The proof does not assume this
# -- it checks it -- but a violation is reported explicitly.
FIRST_NON_CRYPTO_LISTING = "2025-12-11"


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
               .timestamp() * 1000)


def d(x: int) -> str:
    return datetime.fromtimestamp(x / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def ranked_universe(view, cfg: Config) -> list[str]:
    """The universe as weights.compute_target_weights builds it, BEFORE the
    top-N slice: liquidity-ranked, best first. Comparing the full ranking
    means a change below the cut is still a diff."""
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
    return [s for _, s in med]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    ap.add_argument("--start", default=PROOF_START)
    ap.add_argument("--end", default=PROOF_END)
    a = ap.parse_args()

    if ms(a.end) > ms(PROOF_END):
        sys.exit(f"REFUSING: --end {a.end} is past {PROOF_END}. This proof runs "
                 f"on train+validate only; 2025+ is the holdout (NOTES 48.7).")

    cfg = Config(lookback=14, skip=0, vol_target=0.10, initial_capital=800.0,
                 max_liquidity_rank=15, slippage_bps_per_side=5.0)

    print(f"=== Stage 11 3 / NOTES 48.5: the no-op proof ===")
    print(f"window {a.start} -> {a.end}  (train + validate; 2025+ untouched)")
    print(f"comparing the FULL liquidity ranking each day, not just the "
          f"top-{cfg.max_liquidity_rank}\n")

    store = PointInTimeStore(a.db, read_only=True)
    start_ms, end_ms = ms(a.start) + DAY_MS - 1, ms(a.end) + DAY_MS - 1

    n_days = 0
    all_universe_symbols: set[str] = set()
    diffs: list[dict] = []
    ambiguous_seen: dict[str, str] = {}
    fallback_syms: set[str] = set()
    excluded_seen: dict[str, str] = {}
    t0 = time.time()

    t = start_ms
    while t <= end_ms:
        view = store.view_as_of(t)
        unfiltered = ranked_universe(view, cfg)
        all_universe_symbols.update(unfiltered)
        last_bar = {}
        for s_ in unfiltered:
            b_ = view.klines(s_, '1d', limit=1)
            if b_:
                last_bar[s_] = b_[-1].close_time
        filtered, dropped = filter_universe(
            unfiltered, last_bar_ms=last_bar, reference_ms=t)
        for v in dropped:
            excluded_seen.setdefault(v.symbol, v.reason)
            if v.klass == AMBIGUOUS:
                ambiguous_seen.setdefault(v.symbol, v.reason)
        for s in unfiltered:
            if classify(s).reason == "historical_no_metadata":
                fallback_syms.add(s)
        if filtered != unfiltered:
            removed = [s for s in unfiltered if s not in set(filtered)]
            diffs.append({"date": d(t), "removed": removed,
                          "n_unfiltered": len(unfiltered),
                          "n_filtered": len(filtered),
                          "top15_unfiltered": unfiltered[:15],
                          "top15_filtered": filtered[:15]})
        n_days += 1
        if n_days % 250 == 0:
            print(f"  ... {d(t)}  {n_days:,} days, {len(diffs)} diffs so far",
                  flush=True)
        t += DAY_MS
    store.close()

    print(f"\nchecked {n_days:,} days in {time.time() - t0:.1f}s")
    print(f"symbols admitted only by the 48.4 historical fallback "
          f"(absent from the metadata snapshot -- delisted before it): "
          f"{len(fallback_syms):,}")
    if fallback_syms:
        sample = sorted(fallback_syms)
        print(f"  e.g. {', '.join(sample[:12])}"
              + (f" ... (+{len(sample) - 12} more)" if len(sample) > 12 else ""))
        print(f"  reported, not silent: none of these is excluded, so none can "
              f"change the historical universe")
    print(f"symbols the filter excluded at any point in the window: "
          f"{len(excluded_seen)}")
    for sym, why in sorted(excluded_seen.items()):
        print(f"  {sym:<14} {why}")
    print(f"ambiguous (unknown underlyingType) in the window: "
          f"{len(ambiguous_seen)}")

    print(f"\n=== VERDICT (NOTES 48.5, fixed before the run) ===")
    if diffs:
        print(f"  !! {len(diffs):,} DAYS DIFFER -- the amendment is NOT inert.")
        for x in diffs[:10]:
            print(f"    {x['date']}: removed {x['removed']} "
                  f"({x['n_unfiltered']} -> {x['n_filtered']})")
        if len(diffs) > 10:
            print(f"    ... and {len(diffs) - 10:,} more")
        print(f"\n  STOP. A non-crypto instrument was in the historical "
              f"universe, so the 48.2 premise is false and the amendment is "
              f"not free. The decision returns to the user before anything "
              f"else happens (NOTES 48.5).")
        verdict = "DIFFS -- amendment NOT inert; stopped"
    else:
        print(f"  ZERO DIFFS across {n_days:,} days.")
        print(f"  The filtered universe is bit-identical to the unfiltered one "
              f"on every day of train and validate.")
        print(f"  -> the amendment is PROVEN INERT where evidence exists; the "
              f"filtered strategy IS the tested strategy. Nothing needs "
              f"revalidating (NOTES 48.5).")
        verdict = f"INERT -- zero diffs over {n_days} days"

    # Artifact for Test 26: every symbol that appeared in ANY train/validate
    # universe. The test asserts each classifies as crypto, which makes the
    # equivalence permanent without re-running 1,827 days in the suite.
    if not diffs:
        art = ROOT / "data" / "train_validate_universe_symbols.json"
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_text(json.dumps({
            "generated_ts": int(time.time()),
            "window": [a.start, a.end],
            "n_days": n_days,
            "note": "every symbol appearing in any train/validate PIT universe. "
                    "Test 26 asserts the crypto filter excludes none of them.",
            "symbols": sorted(all_universe_symbols),
        }, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {art.relative_to(ROOT)} "
              f"({len(all_universe_symbols)} distinct symbols) for Test 26")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "crypto_filter_noop_proof",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "window": [a.start, a.end], "n_days": n_days,
            "n_diffs": len(diffs), "diffs": diffs[:50],
            "excluded_seen": excluded_seen,
            "ambiguous_seen": ambiguous_seen,
            "n_historical_fallback_symbols": len(fallback_syms),
            "first_non_crypto_listing": FIRST_NON_CRYPTO_LISTING,
            "verdict": verdict,
            "n_universe_symbols": len(all_universe_symbols),
            "note": "train+validate only; no 2025+ data read; no trial",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} "
          f"(kind=crypto_filter_noop_proof)")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
