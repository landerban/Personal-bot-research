#!/usr/bin/env python3
"""
Stage 17 I.2: is a reduce-only order floor-exempt? ASK THE VENUE.

  python tools/floor_probe.py --execute

The model currently disagrees with itself: `plan_rescale` asserts reduce-only
orders are floor-exempt and closes sub-floor positions outright, while
`fillsim` refuses every sub-floor delta including a close. One of them is
wrong about the venue, and the trap is a position whose CLOSING delta is
sub-floor -- unclosable under the strict rule.

STAGE17 "Do not": argue the floor rule from documentation when the venue can
be asked. So this asks it, once, and logs the answer verbatim.

Testnet only, demo=True. Small sizes. Always leaves the account flat.

  1. open ~$15 SOLUSDT
  2. reduce-only PARTIAL close of ~$3        (sub-floor)
  3. reduce to a sub-floor remnant
  4. reduce-only FULL close of that remnant  (sub-floor)
  5. non-reduce-only sub-floor order         (THE CONTROL)

Step 5 is not decoration. Without it, "everything was accepted" cannot
distinguish a floor-exempt reduce-only rule from a venue with no floor today.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from live import reconcile  # noqa: E402
from live.client import TestnetClient, quantize_qty  # noqa: E402

SYMBOL = "SOLUSDT"


def fmt(x) -> str:
    s = f"{Decimal(str(x)):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def attempt(c, label: str, **params) -> dict:
    """Place one order and record verbatim what the venue said."""
    cid = f"probe-{int(time.time() * 1000) % 10 ** 9}"
    try:
        o = c.place_order(newClientOrderId=cid, **params)
        rec = {"label": label, "accepted": True, "status": o.get("status"),
               "executedQty": o.get("executedQty"), "code": None, "msg": None,
               "params": {k: v for k, v in params.items() if k != "symbol"}}
        print(f"  ACCEPTED  {label:<44} status={o.get('status')} "
              f"exec={o.get('executedQty')}")
    except Exception as e:
        rec = {"label": label, "accepted": False, "status": None,
               "code": getattr(e, "code", None), "msg": str(e)[:150],
               "params": {k: v for k, v in params.items() if k != "symbol"}}
        print(f"  REJECTED  {label:<44} code={getattr(e, 'code', None)} "
              f"{str(e)[:70]}")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()

    c = TestnetClient()
    f = c.filters()[SYMBOL]
    bid, ask = c.book(SYMBOL)
    mid = (bid + ask) / 2.0
    floor = float(f.min_notional)
    print(f"=== Stage 17 I.2: floor-exemption probe | TESTNET demo=True ===")
    print(f"    {SYMBOL} mid {mid:.4f}  MIN_NOTIONAL ${floor:.2f}  "
          f"step {f.step_size}")

    q_open = quantize_qty(15.0 / mid, f.step_size)
    q_sub = quantize_qty(3.0 / mid, f.step_size)
    print(f"    open qty {fmt(q_open)} (${float(q_open) * mid:.2f}), "
          f"sub-floor qty {fmt(q_sub)} (${float(q_sub) * mid:.2f})")
    if not a.execute:
        print("\n    DRY RUN -- pass --execute to ask the venue")
        return

    st = reconcile.fetch_state(c)
    if st.positions:
        sys.exit(f"REFUSING: account not flat ({st.positions})")

    results = []
    try:
        # 1. open
        results.append(attempt(
            c, "1 open ~$15 (non-reduce-only)", symbol=SYMBOL, side="BUY",
            type="MARKET", quantity=fmt(q_open)))
        pos = c.positions().get(SYMBOL, 0.0)
        print(f"    position now {pos}")

        # 2. reduce-only PARTIAL close, sub-floor
        results.append(attempt(
            c, "2 reduce-only PARTIAL close ~$3 (sub-floor)", symbol=SYMBOL,
            side="SELL", type="MARKET", quantity=fmt(q_sub),
            reduceOnly="true"))
        pos = c.positions().get(SYMBOL, 0.0)
        print(f"    position now {pos}")

        # 3. reduce down to a sub-floor remnant
        remnant = quantize_qty(2.5 / mid, f.step_size)
        to_shed = quantize_qty(abs(pos) - float(remnant), f.step_size)
        if float(to_shed) > 0:
            results.append(attempt(
                c, "3 reduce-only down to a sub-floor remnant", symbol=SYMBOL,
                side="SELL", type="MARKET", quantity=fmt(to_shed),
                reduceOnly="true"))
        pos = c.positions().get(SYMBOL, 0.0)
        print(f"    remnant {pos} (${abs(pos) * mid:.2f}) vs floor ${floor:.2f}")

        # 4. reduce-only FULL close of a sub-floor remnant -- THE TRAP
        if abs(pos) > 0:
            results.append(attempt(
                c, "4 reduce-only FULL close of sub-floor remnant",
                symbol=SYMBOL, side="SELL", type="MARKET",
                quantity=fmt(abs(pos)), reduceOnly="true"))
            print(f"    position now {c.positions().get(SYMBOL, 0.0)}")

        # 5. THE CONTROL: a sub-floor order that is NOT reduce-only
        results.append(attempt(
            c, "5 CONTROL: sub-floor, NOT reduce-only", symbol=SYMBOL,
            side="BUY", type="MARKET", quantity=fmt(q_sub)))
    finally:
        try:
            c.cancel_all(SYMBOL)
            pos = c.positions().get(SYMBOL, 0.0)
            if abs(pos) > 0:
                c.place_order(symbol=SYMBOL,
                              side="SELL" if pos > 0 else "BUY", type="MARKET",
                              quantity=fmt(abs(pos)), reduceOnly="true",
                              newClientOrderId=f"probe-cl-{int(time.time())}")
        except Exception as e:
            print(f"  cleanup error: {e}")
        final = reconcile.fetch_state(c)
        print(f"\n    final: {final.positions or 'FLAT'}")

    # ---- the verdict -----------------------------------------------------
    by = {r["label"][0]: r for r in results}
    reduce_ok = all(by[k]["accepted"] for k in ("2", "4") if k in by)
    control_rejected = ("5" in by) and not by["5"]["accepted"]

    print(f"\n=== VERDICT ===")
    if reduce_ok and control_rejected:
        rule = "reduce_only_exempt"
        print("  reduce-only sub-floor orders ACCEPTED; the non-reduce-only")
        print("  control was REJECTED -> REDUCE-ONLY IS FLOOR-EXEMPT.")
        print("  plan_rescale was right; fillsim is too strict and must be")
        print("  corrected, or a position could become unclosable in the sim.")
    elif reduce_ok and not control_rejected:
        rule = "no_floor_observed"
        print("  everything was accepted INCLUDING the control -> this venue")
        print("  is not enforcing a floor right now. The probe cannot")
        print("  distinguish an exemption from an absent rule; INCONCLUSIVE.")
    else:
        rule = "floor_applies_to_all"
        print("  reduce-only sub-floor orders were REJECTED -> the floor")
        print("  applies to closes too. fillsim was right; plan_rescale's")
        print("  'reduce-only orders are floor-exempt' comment is WRONG, and")
        print("  a sub-floor position can become unclosable in one order.")
    print(f"  encoded rule: {rule}")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": int(time.time()), "kind": "floor_probe", "demo": True,
            "venue": "testnet", "symbol": SYMBOL, "min_notional": floor,
            "git_commit": runner.git_state()[0], "results": results,
            "rule": rule, "final_flat": not final.positions,
            "note": "Stage 17 I.2; measured, not argued",
        }, default=str) + "\n")
    print(f"  logged to {runner.DIAGNOSTICS_PATH.name} (kind=floor_probe)")


if __name__ == "__main__":
    main()
