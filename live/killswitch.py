#!/usr/bin/env python3
"""
Kill switch: flatten everything on the TESTNET account. Nothing else.

  python live/killswitch.py

Deliberately self-contained: stdlib only, its own signing, its own HTTP.
It shares NO code with live/client.py or live/trader.py, because the failure
it exists for is a trader (or a shared library) that is alive but wedged.
A shared wedge would take the kill switch down with it. The watchdog
imports flatten_all() from here for the same reason.

Runnable from a phone over SSH. Exits non-zero if any position remains.

TESTNET ONLY: the single host below is the testnet host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://testnet.binancefuture.com"
ENV_KEY = "BINANCE_TESTNET_API_KEY"
ENV_SECRET = "BINANCE_TESTNET_API_SECRET"


def _signed(method: str, path: str, params: dict, key: str, secret: str) -> object:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    q = urllib.parse.urlencode(params)
    q += "&signature=" + hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{BASE}{path}?{q}", method=method,
                                 headers={"X-MBX-APIKEY": key})
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code in (429, 418, 500, 502, 503, 504):
                last = RuntimeError(f"{e.code}: {body[:200]}")
                time.sleep(2.0 ** attempt)
                continue
            raise RuntimeError(f"{e.code}: {body[:200]}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2.0 ** attempt)
    raise RuntimeError(f"kill switch request failed after retries: {last}")


def flatten_all(key: str | None = None, secret: str | None = None,
                request=_signed) -> dict:
    """
    Cancel every open order, close every non-zero position with a
    reduce-only MARKET order, re-read positions, and report what is left.
    `request` is injectable for tests.
    """
    key = key or os.environ.get(ENV_KEY, "")
    secret = secret or os.environ.get(ENV_SECRET, "")
    if not key or not secret:
        raise RuntimeError(f"{ENV_KEY}/{ENV_SECRET} not set")

    cancelled, closed, errors = [], [], []

    open_orders = request("GET", "/fapi/v1/openOrders", {}, key, secret)
    for sym in sorted({o["symbol"] for o in open_orders}):
        try:
            request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": sym}, key, secret)
            cancelled.append(sym)
        except Exception as e:  # keep going; every symbol must be attempted
            errors.append(f"cancel {sym}: {e}")

    positions = request("GET", "/fapi/v2/positionRisk", {}, key, secret)
    for p in positions:
        amt = float(p["positionAmt"])
        if amt == 0.0:
            continue
        side = "SELL" if amt > 0 else "BUY"
        try:
            request("POST", "/fapi/v1/order", {
                "symbol": p["symbol"], "side": side, "type": "MARKET",
                "quantity": f"{abs(amt):.10f}".rstrip("0").rstrip("."),
                "reduceOnly": "true",
                "newClientOrderId": f"kill-{int(time.time())}-{p['symbol']}",
            }, key, secret)
            closed.append((p["symbol"], amt))
        except Exception as e:
            errors.append(f"close {p['symbol']}: {e}")

    remaining = {
        p["symbol"]: float(p["positionAmt"])
        for p in request("GET", "/fapi/v2/positionRisk", {}, key, secret)
        if float(p["positionAmt"]) != 0.0
    }
    return {"cancelled": cancelled, "closed": closed, "errors": errors,
            "remaining": remaining, "ts": int(time.time() * 1000)}


def main() -> None:
    result = flatten_all()
    print(json.dumps(result, indent=2))
    if result["remaining"] or result["errors"]:
        print("!! NOT FLAT — intervene manually", file=sys.stderr)
        sys.exit(2)
    print("flat.")


if __name__ == "__main__":
    main()
