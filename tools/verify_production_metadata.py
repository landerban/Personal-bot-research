#!/usr/bin/env python3
"""
Stage 14 Part A verification: does the production snapshot close §48.11?

  python tools/verify_production_metadata.py

Read-only, offline. Both snapshots are user-supplied files already in the
repo; nothing here fetches anything, and no production hostname exists in
this codebase.

Reports, per STAGE14 A.2:
  1. classification counts over EVERY production instrument
  2. the §48.11 seven and the seeded five, and whether METADATA alone
     (seeded list bypassed) now catches them
  3. every symbol where testnet and production metadata DISAGREE -- the
     §48.10 hazard, enumerated at last
  4. the recency guard's ambiguity population, before and after
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from backtest.universe_filter import (  # noqa: E402
    AMBIGUOUS, CRYPTO, EXCLUDED_SYMBOLS, NON_CRYPTO, PRODUCTION_SNAPSHOT_PATH,
    SNAPSHOT_PATH, Verdict, classify, load_snapshot, snapshot_is_stale,
)
import backtest.universe_filter as UF  # noqa: E402

# The 48.11 seven -- found by the recency guard, previously admitted as crypto
SEVEN = ("BZUSDT", "DRAMUSDT", "EWYUSDT", "SAMSUNGUSDT", "MRVLUSDT",
         "AMDUSDT", "NBISUSDT")
# The seeded five -- absent from testnet, caught only by EXCLUDED_SYMBOLS
FIVE = ("SNDKUSDT", "SKHYNIXUSDT", "MUUSDT", "SOXLUSDT", "CLUSDT")


def classify_metadata_only(sym: str, snap: dict) -> Verdict:
    """Classify with the seeded exclusion list bypassed, to find out whether
    the METADATA alone would have caught it (STAGE14 A.2.1)."""
    real = UF.EXCLUDED_SYMBOLS
    UF.EXCLUDED_SYMBOLS = frozenset()
    try:
        return classify(sym, snap)
    finally:
        UF.EXCLUDED_SYMBOLS = real


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "xsmom.db"))
    a = ap.parse_args()

    testnet = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    prod = json.loads(PRODUCTION_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    merged = load_snapshot()

    print("=== Stage 14 A.2: the crypto filter against full production reality ===")
    print(f"  production snapshot  {prod['snapshot_date']}  "
          f"{prod['n_symbols']} symbols")
    print(f"  testnet snapshot     {testnet['snapshot_date']}  "
          f"{testnet['n_symbols']} symbols")
    print(f"  merged view          {len(merged['symbols'])} symbols "
          f"({merged.get('n_testnet_only')} testnet-only)")
    print(f"  stale (>30d)?        {snapshot_is_stale()}\n")

    # ---- 1. classification counts over every production instrument -------
    counts = Counter()
    reasons = Counter()
    for sym in prod["symbols"]:
        v = classify(sym, merged)
        counts[v.klass] += 1
        reasons[v.reason.split(":")[0].split(" (")[0]] += 1
    print("=== 1. classification of every production instrument ===")
    for k in (CRYPTO, NON_CRYPTO, AMBIGUOUS):
        print(f"  {k:<12} {counts.get(k, 0):>4}")
    print("  by reason:")
    for r, n in reasons.most_common():
        print(f"    {r:<44} {n:>4}")

    # ---- 2. the seven and the five --------------------------------------
    print(f"\n=== 2. the §48.11 seven -- must classify BY METADATA now ===")
    ok_seven = True
    for s in SEVEN:
        v = classify(s, merged)
        m = classify_metadata_only(s, merged)
        by_meta = m.klass == NON_CRYPTO and m.from_metadata
        ok_seven &= by_meta
        print(f"  {s:<14} {v.klass:<11} {'METADATA' if by_meta else 'NOT by metadata'}"
              f"   {m.reason}")

    print(f"\n=== 2b. the seeded five -- would metadata alone catch them? ===")
    ok_five = True
    for s in FIVE:
        m = classify_metadata_only(s, merged)
        by_meta = m.klass == NON_CRYPTO and m.from_metadata
        ok_five &= by_meta
        print(f"  {s:<14} {'METADATA' if by_meta else 'SEEDED LIST ONLY':<16} "
              f"{m.reason}")
    print(f"  -> the seeded list is now {'REDUNDANT (belt and braces at last)'
          if ok_five else 'STILL LOAD-BEARING'}")

    # ---- 3. testnet vs production disagreements -------------------------
    print(f"\n=== 3. symbols where TESTNET and PRODUCTION metadata disagree ===")
    both = set(testnet["symbols"]) & set(prod["symbols"])
    disagree = []
    for sym in sorted(both):
        t = classify(sym, {"symbols": testnet["symbols"]})
        p = classify(sym, {"symbols": prod["symbols"]})
        if t.klass != p.klass:
            disagree.append((sym, t, p))
    if disagree:
        for sym, t, p in disagree:
            print(f"  {sym:<14} testnet={t.klass:<11} ({t.reason})")
            print(f"  {'':<14} prod   ={p.klass:<11} ({p.reason})")
    else:
        print(f"  none -- the two sources agree on all {len(both)} shared symbols")
    print(f"  (checked {len(both)} symbols present in both snapshots)")

    # ---- 4. recency-guard ambiguity, before and after --------------------
    print(f"\n=== 4. the §48.11 recency guard: ambiguity population ===")
    prod_only_syms = set(prod["symbols"]) - set(testnet["symbols"])
    print(f"  symbols production carries that testnet does NOT: "
          f"{len(prod_only_syms)}")
    before = [s for s in prod_only_syms
              if classify(s, {"symbols": testnet["symbols"]}).reason
              == "historical_no_metadata"]
    after = [s for s in prod_only_syms if classify(s, merged).reason
             == "historical_no_metadata"]
    print(f"  invisible to the testnet-only classifier (fell through as "
          f"'crypto'):  {len(before)}")
    print(f"  still invisible with production metadata:                       "
          f"    {len(after)}")
    tradfi_hidden = [s for s in before
                     if classify(s, merged).klass == NON_CRYPTO]
    print(f"  of the previously-invisible, now positively TradFi:             "
          f"    {len(tradfi_hidden)}")
    if tradfi_hidden:
        print(f"    e.g. {', '.join(sorted(tradfi_hidden)[:14])}"
              + (f" ... (+{len(tradfi_hidden) - 14} more)"
                 if len(tradfi_hidden) > 14 else ""))

    verdict = ("CLOSED" if (ok_seven and not after and not disagree)
               else "PARTIAL")
    print(f"\n=== VERDICT ===")
    print(f"  §48.14.1 (production metadata gap): {verdict}")
    print(f"    the seven classify by metadata     : {ok_seven}")
    print(f"    seeded five redundant              : {ok_five}")
    print(f"    testnet/production disagreements   : {len(disagree)}")
    print(f"    still-invisible production symbols : {len(after)}")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "production_metadata_verify",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "production_snapshot_date": prod["snapshot_date"],
            "testnet_snapshot_date": testnet["snapshot_date"],
            "counts": dict(counts), "reasons": dict(reasons),
            "seven_by_metadata": ok_seven, "five_redundant": ok_five,
            "disagreements": [{"symbol": s, "testnet": t.klass, "prod": p.klass,
                               "testnet_reason": t.reason, "prod_reason": p.reason}
                              for s, t, p in disagree],
            "prod_only_symbols": len(prod_only_syms),
            "invisible_before": len(before), "invisible_after": len(after),
            "newly_caught_tradfi": sorted(tradfi_hidden),
            "verdict": verdict,
            "note": "offline; both snapshots are user-supplied files; no fetch",
        }) + "\n")
    print(f"\nlogged to {runner.DIAGNOSTICS_PATH.name} "
          f"(kind=production_metadata_verify)")


if __name__ == "__main__":
    main()
