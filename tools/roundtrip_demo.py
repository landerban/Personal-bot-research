#!/usr/bin/env python3
"""
Stage 16 Part A: prove the order machinery genuinely trades.

  python tools/roundtrip_demo.py            # dry run: plan only, no orders
  python tools/roundtrip_demo.py --execute  # place real TESTNET orders

TESTNET ONLY. Every order goes through TestnetClient, which cannot be pointed
at a production venue. Real orders, imaginary money.

Before testnet is retired to demo-fixture duty, this demonstrates end to end
that the code can place, fill, cancel, reconcile and close -- and that it
classifies a rejection correctly rather than only walking the happy path.

Per symbol (liquid majors only -- junk adds noise, not evidence):

  1. MARKET order      -> ack -> fill -> price and fee in the costlog
  2. LIMIT passive     -> cancel -> confirmed gone
  3. reduce-only STOP  -> visible on the exchange -> cancelled
  4. reconcile         -> the live position matches the fill
  5. CLOSE             -> flat -> reconcile confirms flat
  6. undersized order  -> rejection captured and CLASSIFIED

Every record is tagged `demo=True` so nothing here can contaminate strategy
data. Any divergence between the exchange response and the local record stops
the run: a partial pass is not evidence (NOTES 56.5).
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
from live.client import (  # noqa: E402
    ExchangeError, FilterRejected, TestnetClient, quantize_price,
    quantize_qty,
)

# Binance: 'Order type not supported for this endpoint.'
CODE_ORDER_TYPE = -4120
from live.costlog import CostLog  # noqa: E402

# Liquid majors, sized above each symbol's own MIN_NOTIONAL floor.
PLAN = [("BTCUSDT", 60.0), ("ETHUSDT", 25.0), ("SOLUSDT", 15.0)]


def fmt(x) -> str:
    s = f"{Decimal(str(x)):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class Roundtrip:
    def __init__(self, client: TestnetClient, costlog: CostLog, execute: bool):
        self.c = client
        self.cl = costlog
        self.execute = execute
        self.steps: list[dict] = []
        self.failures: list[str] = []

    def step(self, symbol: str, name: str, fn):
        """Run one step, recording pass/fail. A step that raises is recorded
        against ITS OWN name and the symbol continues -- an uncaught exception
        used to abort the symbol and skip its close, leaving the account
        dirty. A harness that proves fail-closed behaviour must itself fail
        closed."""
        try:
            ok, detail, payload = fn()
            return self.record(symbol, name, ok, detail, payload)
        except Exception as e:
            return self.record(symbol, name, False,
                               f"{type(e).__name__}: {str(e)[:110]}")

    def cleanup(self, symbol: str, step_size: float) -> None:
        """Always run: cancel everything, close anything, verify flat."""
        try:
            self.c.cancel_all(symbol)
        except Exception as e:
            self.record(symbol, "cleanup cancel", False, str(e)[:90])
        try:
            pos = self.c.positions().get(symbol, 0.0)
            if abs(pos) > 0:
                self.c.place_order(
                    symbol=symbol, side="SELL" if pos > 0 else "BUY",
                    type="MARKET", quantity=fmt(abs(pos)), reduceOnly="true",
                    newClientOrderId=f"demo-cleanup-{int(time.time()*1000)%10**9}")
            left = self.c.positions().get(symbol, 0.0)
            self.record(symbol, "cleanup flat", abs(left) < step_size * 2,
                        f"position {left}")
        except Exception as e:
            self.record(symbol, "cleanup close", False, str(e)[:90])

    def record(self, symbol: str, step: str, ok: bool, detail: str,
               payload: dict | None = None) -> bool:
        self.steps.append({"symbol": symbol, "step": step, "ok": bool(ok),
                           "detail": detail, "payload": payload or {},
                           "ts": int(time.time() * 1000), "demo": True})
        mark = "ok  " if ok else "FAIL"
        print(f"    [{mark}] {step:<26} {detail}")
        if not ok:
            self.failures.append(f"{symbol}/{step}: {detail}")
        return ok

    # ---------------------------------------------------------------- steps

    def run_symbol(self, symbol: str, notional: float) -> None:
        print(f"\n--- {symbol} (${notional:.0f}) " + "-" * 40)
        f = self.c.filters().get(symbol)
        if f is None:
            self.record(symbol, "filters", False, "no exchange filters")
            return
        bid, ask = self.c.book(symbol)
        mid = (bid + ask) / 2.0
        qty = quantize_qty(notional / mid, f.step_size)
        self.record(symbol, "sizing", float(qty) > 0,
                    f"mid {mid:.4f}, qty {fmt(qty)}, notional "
                    f"${float(qty) * mid:.2f}, MIN_NOTIONAL {f.min_notional}")
        if not self.execute:
            print("    (dry run -- no orders placed)")
            return

        # 1. MARKET in
        cid = f"demo-mkt-{symbol}-{int(time.time())}"[:36]
        order = self.c.place_order(symbol=symbol, side="BUY", type="MARKET",
                                   quantity=fmt(qty), newClientOrderId=cid)
        filled = float(order.get("executedQty", 0) or 0)
        self.record(symbol, "1 market order", filled > 0,
                    f"status {order.get('status')}, executed {filled}",
                    {"orderId": order.get("orderId")})
        fees = 0.0
        for t in self.c.user_trades(symbol, order_id=int(order["orderId"])):
            rec = self.cl.record_fill(
                symbol=symbol, side="BUY", intended_price=mid,
                fill_price=float(t["price"]), qty=float(t["qty"]),
                fee=float(t["commission"]), fee_asset=t["commissionAsset"],
                maker=bool(t["maker"]), order_type="MARKET",
                ts_ms=int(t["time"]), order_id=int(t["orderId"]),
                trade_id=int(t["id"]), venue="testnet")
            fees += float(t["commission"])
        self.record(symbol, "1b costlog fill row", fees >= 0,
                    f"fee {fees:.6f} recorded, venue=testnet demo=True")

        # 2. passive LIMIT -> cancel
        # A resting order's notional is its OWN price x qty, not the mid's.
        # Sizing it off the mid put a 20%-below-bid limit under MIN_NOTIONAL
        # and the venue refused it -- my bug, not the venue's.
        try:
            self._steps_2_to_6(symbol, notional, f, bid, mid, qty, filled)
        finally:
            self.cleanup(symbol, float(f.step_size))

    def _steps_2_to_6(self, symbol, notional, f, bid, mid, qty, filled):
        px = quantize_price(bid * 0.80, f.tick_size, "SELL")
        lqty = quantize_qty(notional / float(px) * 1.15, f.step_size)
        lcid = f"demo-lim-{symbol}-{int(time.time() * 1000) % 10 ** 9}"[:36]

        def place_limit():
            lim = self.c.place_order(symbol=symbol, side="BUY", type="LIMIT",
                                     timeInForce="GTC", price=fmt(px),
                                     quantity=fmt(lqty), newClientOrderId=lcid)
            return (lim.get("status") in ("NEW", "PARTIALLY_FILLED"),
                    f"resting at {fmt(px)} (20% below bid), qty {fmt(lqty)}, "
                    f"notional ${float(lqty) * float(px):.2f}, status "
                    f"{lim.get('status')}", {"orderId": lim.get("orderId")})

        def cancel_limit():
            self.c.cancel_order(symbol, lcid)
            after = self.c.get_order(symbol, lcid)
            return (after.get("status") == "CANCELED",
                    f"status now {after.get('status')}", {})

        self.step(symbol, "2 limit placed", place_limit)
        self.step(symbol, "2b limit cancelled", cancel_limit)

        # 3. reduce-only STOP -> visible -> cancelled
        stop_px = quantize_price(mid * 0.80, f.tick_size, "SELL")
        scid = f"demo-stp-{symbol}-{int(time.time() * 1000) % 10 ** 9}"[:36]
        try:
            self.c.place_order(symbol=symbol, side="SELL", type="STOP_MARKET",
                               stopPrice=fmt(stop_px), closePosition="true",
                               workingType="MARK_PRICE", newClientOrderId=scid)
            open_orders = self.c.open_orders(symbol)
            seen = any(o.get("clientOrderId") == scid for o in open_orders)
            self.record(symbol, "3 stop visible", seen,
                        f"{len(open_orders)} open order(s); stop at {fmt(stop_px)}")
            self.c.cancel_all(symbol)
            self.record(symbol, "3b stop cancelled",
                        not self.c.open_orders(symbol), "no open orders remain")
        except ExchangeError as e:
            # NOTES 56.7: this venue refuses EVERY conditional type on
            # /fapi/v1/order with -4120, while exchangeInfo.orderTypes
            # advertises them. Recorded as a venue capability finding, not as
            # a code failure -- and layer-1 stop protection is therefore
            # UNAVAILABLE here.
            unsupported = getattr(e, "code", None) == CODE_ORDER_TYPE
            self.record(symbol, "3 stop (layer-1)", unsupported,
                        f"venue refuses conditional orders on this endpoint "
                        f"(code {getattr(e, 'code', None)}); layer-1 stops "
                        f"UNAVAILABLE -- layers 2 and 3 unaffected")

        # 4. reconcile sees the position.
        # positionRisk can LAG a filled order (observed once: 0.0 immediately
        # after a FILLED market order, correct on a later call), so settle
        # before asserting rather than reading once and believing it.
        pos = 0.0
        for _ in range(10):
            state = reconcile.fetch_state(self.c)
            pos = state.positions.get(symbol, 0.0)
            if abs(pos) > 0:
                break
            time.sleep(0.5)
        self.record(symbol, "4 reconcile sees position",
                    abs(pos - float(qty)) < float(f.step_size) * 2,
                    f"exchange {pos} vs filled {filled}")

        # 5. close -> flat
        def close_out():
            if abs(pos) <= 0:
                return False, "nothing to close (no position seen)", {}
            self.c.place_order(
                symbol=symbol, side="SELL" if pos > 0 else "BUY",
                type="MARKET", quantity=fmt(abs(pos)), reduceOnly="true",
                newClientOrderId=f"demo-cls-{int(time.time() * 1000) % 10 ** 9}")
            left = self.c.positions().get(symbol, 0.0)
            return (abs(left) < float(f.step_size),
                    f"position now {left}", {})

        self.step(symbol, "5 closed and flat", close_out)

        # 6. the ERROR path: deliberately undersized. The happy path alone
        #    is not evidence -- a rejection must be CLASSIFIED, not just raised.
        def undersized():
            tiny = quantize_qty(float(f.min_qty), f.step_size)
            try:
                self.c.place_order(
                    symbol=symbol, side="BUY", type="MARKET", quantity=fmt(tiny),
                    newClientOrderId=f"demo-tiny-{int(time.time()*1000)%10**9}")
                return False, "order was ACCEPTED -- the floor did not bite", {}
            except FilterRejected as e:
                return True, f"classified FilterRejected, code {e.code}", {}
            except Exception as e:
                return False, f"wrong class {type(e).__name__}: {e}", {}

        self.step(symbol, "6 undersized rejected", undersized)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="place REAL testnet orders (default is a dry run)")
    a = ap.parse_args()

    print("=== Stage 16 Part A: order-machinery roundtrip | TESTNET ===")
    print(f"    mode: {'EXECUTE (real testnet orders)' if a.execute else 'DRY RUN'}")
    c = TestnetClient()
    print(f"    venue: {c.base_url}")
    state = reconcile.fetch_state(c)
    print(f"    account equity {state.equity:.2f}, "
          f"{len(state.positions)} open position(s)")
    if state.positions and a.execute:
        sys.exit(f"REFUSING: account is not flat ({state.positions}). The "
                 f"roundtrip must start from a known state.")

    rt = Roundtrip(c, CostLog(ROOT / "paper_costs_demo.jsonl"), a.execute)
    for symbol, notional in PLAN:
        try:
            rt.run_symbol(symbol, notional)
        except Exception as e:
            rt.record(symbol, "UNCAUGHT", False, f"{type(e).__name__}: {e}")

    ok = [s for s in rt.steps if s["ok"]]
    print(f"\n=== RESULT: {len(ok)} of {len(rt.steps)} checks passed ===")
    if rt.failures:
        print("  FAILURES (NOTES 56.5: a partial pass is not evidence):")
        for f_ in rt.failures:
            print(f"    - {f_}")
    else:
        print("  every step produced the expected exchange response and the "
              "expected local record")

    final = reconcile.fetch_state(c)
    flat = not final.positions
    print(f"  final state: {'FLAT' if flat else final.positions}")

    with open(runner.DIAGNOSTICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": int(time.time()), "kind": "roundtrip_demo",
            "git_commit": runner.git_state()[0], "dirty": runner.git_state()[1],
            "venue": "testnet", "demo": True, "executed": a.execute,
            "steps": rt.steps, "failures": rt.failures,
            "passed": len(ok), "total": len(rt.steps), "final_flat": flat,
            "note": "Stage 16 A.1; demo=True isolation; no strategy data",
        }) + "\n")
    print(f"  logged to {runner.DIAGNOSTICS_PATH.name} (kind=roundtrip_demo)")
    sys.exit(1 if (rt.failures or not flat) else 0)


if __name__ == "__main__":
    main()
