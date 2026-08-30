"""
Offline tests for the paper-trading harness against a fake exchange.

No network. The fake implements the slice of the USDS-M REST surface the
harness uses, with the exchange's real rejection codes, and lets tests
inject the failures from STAGE2B B5 that can be simulated in-process:
timestamp rejection (-1021), rate limiting (429 + Retry-After),
unquantised quantity (-1013), sub-MIN_NOTIONAL (-4164), an exception
mid-rebalance (fail closed), a stale heartbeat (watchdog), unknown
positions at startup (reconcile). The physical injections (pull the cable,
kill -9) are in live/RUNBOOK.md and must be performed by a person.
"""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.costs import funding_cashflow  # noqa: E402
from live import costlog as CL  # noqa: E402
from live import killswitch, reconcile, watchdog  # noqa: E402
from live.client import (  # noqa: E402
    FilterRejected, RateLimited, TestnetClient, TimestampRejected,
    quantize_price, quantize_qty, sign,
)
from live.trader import PaperConfig, Trader  # noqa: E402

KEY, SECRET = "test-key", "test-secret"
DAY_MS = 86_400_000


# ------------------------------------------------------------- fake exchange

class FakeExchange:
    """In-memory USDS-M testnet. Fills MARKET at bid/ask, GTX LIMIT at the
    limit price as maker; rejects with Binance's real codes."""

    FILTERS = {
        "ETHUSDT": ("0.001", "0.01", "5", "0.001"),
        "BNBUSDT": ("0.01", "0.001", "5", "0.01"),
        "BTCUSDT": ("0.001", "0.1", "50", "0.001"),
        "SOLUSDT": ("1", "0.001", "5", "1"),
    }
    PRICES = {"ETHUSDT": (3000.0, 3001.0), "BNBUSDT": (300.0, 300.1),
              "BTCUSDT": (60000.0, 60010.0), "SOLUSDT": (150.0, 150.1)}

    def __init__(self, now_s: float = 1_780_000_000.0):
        self.now = now_s
        self.server_offset_ms = 0
        self.recv_window = 5000
        self.positions = {s: 0.0 for s in self.FILTERS}
        self.open_orders: list[dict] = []
        self.trades: list[dict] = []
        self.income: list[dict] = []
        self.equity = 10_000.0
        self.dual = False
        self.fail_next: list[tuple[int, dict, dict]] = []
        self.reject_timestamp_n = 0
        self.raise_on_order: Exception | None = None
        self.maker_no_fill = False
        self._oid = 1000
        self._tid = 1
        self._done: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        rng = np.random.default_rng(7)
        n = 80
        r_btc = rng.normal(0, 0.02, n)
        self._klines = {}
        t0 = int(now_s * 1000) // DAY_MS * DAY_MS - n * DAY_MS
        for sym, beta, p0 in (("BTCUSDT", 1.0, 60000.0), ("ETHUSDT", 1.1, 3000.0),
                              ("BNBUSDT", 0.9, 300.0), ("SOLUSDT", 1.3, 150.0)):
            r = beta * r_btc + (rng.normal(0, 0.03, n) if sym != "BTCUSDT" else 0)
            closes = p0 * np.cumprod(1 + r)
            self._klines[sym] = [
                [t0 + i * DAY_MS, closes[i - 1] if i else p0, closes[i] * 1.01,
                 closes[i] * 0.99, closes[i], 1.0, t0 + (i + 1) * DAY_MS - 1]
                for i in range(n)
            ]

    def now_ms(self) -> int:
        return int(self.now * 1000) + self.server_offset_ms

    # transport signature expected by TestnetClient
    def __call__(self, method, path, params, signed):
        self.requests.append((method, path))
        if self.fail_next:
            return self.fail_next.pop(0)
        if signed:
            assert "signature" in params and "timestamp" in params, "unsigned signed call"
            q = "&".join(f"{k}={v}" for k, v in params.items() if k != "signature")
            import urllib.parse
            expect = sign(SECRET, urllib.parse.urlencode(
                {k: v for k, v in params.items() if k != "signature"}))
            assert params["signature"] == expect, "bad signature"
            if self.reject_timestamp_n > 0 or abs(int(params["timestamp"]) - self.now_ms()) > self.recv_window:
                self.reject_timestamp_n = max(self.reject_timestamp_n - 1, 0)
                return 400, {}, {"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."}
        h = {"X-MBX-USED-WEIGHT-1M": "12"}
        try:
            return 200, h, self._route(method, path, params)
        except _Reject as e:
            return 400, h, {"code": e.code, "msg": e.msg}

    def _route(self, method, path, p):
        if path == "/fapi/v1/time":
            return {"serverTime": self.now_ms()}
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [
                {"symbol": s, "status": "TRADING", "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": st, "minQty": mq},
                    {"filterType": "PRICE_FILTER", "tickSize": tk},
                    {"filterType": "MIN_NOTIONAL", "notional": mn}]}
                for s, (st, tk, mn, mq) in self.FILTERS.items()]}
        if path == "/fapi/v1/ticker/bookTicker":
            b, a = self.PRICES[p["symbol"]]
            return {"bidPrice": str(b), "askPrice": str(a)}
        if path == "/fapi/v1/premiumIndex":
            b, a = self.PRICES[p["symbol"]]
            return {"markPrice": str((a + b) / 2)}
        if path == "/fapi/v1/klines":
            return self._klines[p["symbol"]][-int(p["limit"]):]
        if path == "/fapi/v1/fundingRate":
            return [{"fundingTime": p["startTime"] + 60_000, "fundingRate": "0.0001"}]
        if path == "/fapi/v1/positionSide/dual":
            return {"dualSidePosition": self.dual}
        if path == "/fapi/v2/account":
            return {"totalMarginBalance": str(self.equity)}
        if path == "/fapi/v2/positionRisk":
            return [{"symbol": s, "positionAmt": f"{u:.6f}", "positionSide": "BOTH",
                     "entryPrice": "0", "markPrice": str(sum(self.PRICES[s]) / 2)}
                    for s, u in self.positions.items()]
        if path == "/fapi/v1/openOrders":
            return [o for o in self.open_orders if not p.get("symbol") or o["symbol"] == p["symbol"]]
        if path == "/fapi/v1/allOpenOrders" and method == "DELETE":
            self.open_orders = [o for o in self.open_orders if o["symbol"] != p["symbol"]]
            return {"code": 200, "msg": "The operation of cancel all open order is done."}
        if path == "/fapi/v1/order" and method == "DELETE":
            # Binance keeps a cancelled order queryable; only remove it from
            # the open set.
            for o in self.open_orders:
                if o["clientOrderId"] == p["origClientOrderId"]:
                    o["status"] = "CANCELED"
                    self._done.append(o)
            self.open_orders = [o for o in self.open_orders if o["status"] == "NEW"]
            return {"status": "CANCELED"}
        if path == "/fapi/v1/order" and method == "GET":
            for o in self.open_orders + self._done:
                if o["clientOrderId"] == p["origClientOrderId"]:
                    return o
            raise _Reject(-2013, "Order does not exist.")
        if path == "/fapi/v1/order" and method == "POST":
            if self.raise_on_order is not None:
                raise self.raise_on_order
            return self._place(p)
        if path == "/fapi/v1/userTrades":
            return [t for t in self.trades if t["symbol"] == p["symbol"]
                    and (p.get("orderId") is None or t["orderId"] == int(p["orderId"]))]
        if path == "/fapi/v1/income":
            return [i for i in self.income if i["incomeType"] == p["incomeType"]]
        if path == "/fapi/v1/listenKey":
            return {"listenKey": "fake-listen-key"}
        raise AssertionError(f"unrouted {method} {path}")

    def _place(self, p):
        sym = p["symbol"]
        step, tick, mn, mq = (Decimal(x) for x in self.FILTERS[sym])
        self._oid += 1
        order = {"orderId": self._oid, "clientOrderId": p.get("newClientOrderId", str(self._oid)),
                 "symbol": sym, "side": p["side"], "type": p["type"], "status": "NEW",
                 "executedQty": "0", "avgPrice": "0"}
        if p["type"] == "STOP_MARKET":
            assert p.get("closePosition") == "true"
            self.open_orders.append(order)
            return order
        qty = Decimal(str(p["quantity"]))
        if (qty / step) != (qty / step).to_integral_value():
            raise _Reject(-1013, "Filter failure: LOT_SIZE")
        bid, ask = self.PRICES[sym]
        reduce_only = p.get("reduceOnly") == "true"
        if p["type"] == "LIMIT":
            assert p.get("timeInForce") == "GTX"
            price = Decimal(str(p["price"]))
            if (p["side"] == "BUY" and price >= Decimal(str(ask))) or \
               (p["side"] == "SELL" and price <= Decimal(str(bid))):
                raise _Reject(-5022, "Post Only order will be rejected")
            if self.maker_no_fill:
                self.open_orders.append(order)
                return order
            fill_price, maker, rate = float(price), True, 0.0002
        else:
            fill_price, maker, rate = (ask if p["side"] == "BUY" else bid), False, 0.0005
        notional = float(qty) * fill_price
        if not reduce_only and Decimal(str(notional)) < mn:
            raise _Reject(-4164, f"Order's notional must be no smaller than {mn} (unless you choose reduce only)")
        signed = float(qty) if p["side"] == "BUY" else -float(qty)
        if reduce_only:
            cur = self.positions[sym]
            signed = -math.copysign(min(abs(signed), abs(cur)), cur) if cur else 0.0
        self.positions[sym] = round(self.positions[sym] + signed, 8)
        self._tid += 1
        self.trades.append({"id": self._tid, "orderId": self._oid, "symbol": sym,
                            "side": p["side"], "price": str(fill_price), "qty": str(abs(signed)),
                            "commission": str(abs(signed) * fill_price * rate),
                            "commissionAsset": "USDT", "maker": maker, "time": self.now_ms()})
        order.update(status="FILLED", executedQty=str(abs(signed)), avgPrice=str(fill_price))
        self._done.append(order)
        return order


class _Reject(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


def make_env():
    fx = FakeExchange()
    sleeps: list[float] = []

    def sleeper(s):
        sleeps.append(s)
        fx.now += s

    client = TestnetClient(KEY, SECRET, transport=fx, sleeper=sleeper,
                           clock=lambda: fx.now, max_retries=5)
    scratch = Path(tempfile.mkdtemp())
    cfg = PaperConfig(use_stream=False)
    return fx, client, sleeps, scratch, cfg


def make_trader(fx, client, scratch, cfg):
    return Trader(client, cfg, costlog=CL.CostLog(scratch / "costs.jsonl"),
                  paper_log=scratch / "paper_log.jsonl",
                  heartbeat=scratch / "heartbeat", sleeper=client._sleep,
                  clock=lambda: fx.now)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ------------------------------------------------------------------- tests

def test_signature_matches_binance_docs_vector():
    """The published example (developers.binance.com, endpoint-security-type):
    same HMAC-SHA256 scheme on every Binance API. Vector verified 2026-08-27."""
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    q = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
         "&recvWindow=5000&timestamp=1499827319559")
    assert sign(secret, q) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    print("PASS signature_matches_binance_docs_vector")


# NOTES 56.1: the ONLY module permitted to name a production host. It is
# read-only, unsigned and GET-only, and test_proddata_cannot_sign_or_post
# proves each of those separately. Adding a name here without those proofs is
# the thing this list exists to make visible in a diff.
DATA_ONLY_MODULES = {"proddata.py"}


def test_no_mainnet_anywhere_in_live():
    """B1/B7, NARROWED by NOTES 56.1 -- not relaxed.

    Every TRADING-capable module must still contain no production host, not
    even commented out. The single data-only module may, because it cannot
    sign and therefore cannot reach a venue that settles money -- which was
    always the rule's purpose.
    """
    mainnet = re.compile(r"(?<!testnet\.)(?<![a-z])(f?api|f?stream|ws-f?api)\.binance\.com")
    checked = 0
    for f in (Path(__file__).resolve().parents[1] / "live").glob("*.py"):
        if f.name in DATA_ONLY_MODULES:
            continue
        checked += 1
        txt = f.read_text(encoding="utf-8")
        assert not mainnet.search(txt), f"mainnet host in {f.name}"
    assert checked >= 6, f"only {checked} trading modules scanned"
    # and the trading client specifically must be unable to name one
    for f in ("client.py", "killswitch.py", "trader.py", "phase2.py"):
        txt = (Path(__file__).resolve().parents[1] / "live" / f).read_text(
            encoding="utf-8")
        assert not mainnet.search(txt), f"mainnet host in {f}"
    for f in ("client.py", "killswitch.py"):
        assert "testnet.binancefuture.com" in (Path(__file__).resolve().parents[1] / "live" / f).read_text()
    print("PASS no_mainnet_anywhere_in_live")


def test_quantisation():
    assert quantize_qty(0.0073333, Decimal("0.001")) == Decimal("0.007")
    assert quantize_qty(-0.0079, Decimal("0.001")) == Decimal("0.007")  # floors |qty|
    assert quantize_qty(12.34, Decimal("1")) == Decimal("12")
    assert quantize_price(2999.996, Decimal("0.01"), "SELL") == Decimal("2999.99")
    assert quantize_price(2999.991, Decimal("0.01"), "BUY") == Decimal("3000.00")
    print("PASS quantisation")


def test_rate_limit_backoff_honours_retry_after():
    fx, client, sleeps, _, _ = make_env()
    fx.fail_next = [
        (429, {"Retry-After": "3"}, {"code": -1003, "msg": "Too many requests"}),
        (429, {}, {"code": -1003, "msg": "Too many requests"}),
        (503, {}, {"code": None, "msg": "unavailable"}),
    ]
    assert client.server_time() > 0
    assert client.backoffs == [3.0, 2.0, 4.0], client.backoffs  # never shorter than exp
    fx.fail_next = [(429, {}, {"code": -1003, "msg": "x"})] * 6
    try:
        client.server_time()
    except RateLimited:
        pass
    else:
        raise AssertionError("must give up, not hammer forever")
    print("PASS rate_limit_backoff_honours_retry_after")


def test_timestamp_rejection_resyncs_once():
    fx, client, _, _, _ = make_env()
    fx.server_offset_ms = 400_000  # local clock 400s behind the exchange
    assert client.positions() == {}   # -1021 -> resync -> retry -> ok
    assert client.timestamp_resyncs == 1
    fx.reject_timestamp_n = 2         # persistent rejection is a real fault
    try:
        client.positions()
    except TimestampRejected:
        pass
    else:
        raise AssertionError("second -1021 in one request must raise")
    print("PASS timestamp_rejection_resyncs_once")


def test_rebalance_sizes_through_backtest_weights_and_places_stops():
    fx, client, _, scratch, cfg = make_env()
    tr = make_trader(fx, client, scratch, cfg)
    out = tr.rebalance_once()
    pos = fx.positions
    assert pos["ETHUSDT"] > 0 and pos["BNBUSDT"] < 0, pos
    for sym in ("ETHUSDT", "BNBUSDT"):
        notional = abs(pos[sym]) * sum(fx.PRICES[sym]) / 2
        assert 5.0 <= notional <= cfg.equity_cap * cfg.max_gross
    # sizing came from the SAME functions the backtester uses
    wlog = [r for r in read_jsonl(scratch / "paper_log.jsonl") if r["kind"] == "weights"][0]
    assert abs(wlog["final"]["ETHUSDT"]) * min(fx.equity, cfg.equity_cap) >= 5
    assert wlog["final"]["BNBUSDT"] < 0
    # exchange-side stops exist for both positions
    stops = [o for o in fx.open_orders if o["type"] == "STOP_MARKET"]
    assert {o["symbol"] for o in stops} == {"ETHUSDT", "BNBUSDT"}
    # cost log: taker fills, slippage vs mid positive (paid the spread)
    fills = [r for r in tr.costlog.records() if r["kind"] == "fill"]
    assert len(fills) == 2 and all(not f["maker"] for f in fills)
    assert all(f["slippage_bps"] > 0 for f in fills)
    for f in fills:
        assert math.isclose(f["fee"], f["notional"] * 0.0005, rel_tol=1e-9)
    assert "pnl" not in json.dumps(out).lower()
    print("PASS rebalance_sizes_through_backtest_weights_and_places_stops")


def test_restart_reconciles_and_does_not_double_position():
    fx, client, _, scratch, cfg = make_env()
    tr = make_trader(fx, client, scratch, cfg)
    tr.rebalance_once()
    before = dict(fx.positions)
    n_trades = len(fx.trades)
    tr2 = make_trader(fx, client, scratch, cfg)  # "restart": fresh object, no local state
    out = tr2.rebalance_once()
    assert out["deltas"] == {}, out["deltas"]
    assert fx.positions == before and len(fx.trades) == n_trades
    print("PASS restart_reconciles_and_does_not_double_position")


def test_below_min_notional_rejected_logged_no_crash():
    fx, client, _, scratch, _ = make_env()
    cfg = PaperConfig(use_stream=False, inject="below-min-notional")
    tr = make_trader(fx, client, scratch, cfg)
    tr.rebalance_once()
    assert tr.stats.orders_rejected == 2
    assert all(u == 0 for u in fx.positions.values())
    rej = [r for r in read_jsonl(scratch / "paper_log.jsonl") if r["kind"] == "order_rejected"]
    assert {r["code"] for r in rej} == {-4164}
    # and the LOCAL check catches it first when not injecting
    cfg2 = PaperConfig(use_stream=False, equity_cap=8.0)  # legs ~ $2 each
    tr2 = make_trader(fx, client, scratch, cfg2)
    out = tr2.rebalance_once()
    assert out == {"skipped": "below_min_notional"}
    assert all(u == 0 for u in fx.positions.values())
    print("PASS below_min_notional_rejected_logged_no_crash")


def test_unquantised_quantity_rejected_logged_no_crash():
    fx, client, _, scratch, _ = make_env()
    tr = make_trader(fx, client, scratch, PaperConfig(use_stream=False, inject="unquantised"))
    tr.rebalance_once()
    assert tr.stats.orders_rejected == 2
    rej = [r for r in read_jsonl(scratch / "paper_log.jsonl") if r["kind"] == "order_rejected"]
    assert {r["code"] for r in rej} == {-1013}
    print("PASS unquantised_quantity_rejected_logged_no_crash")


def test_clock_skew_injection_handled_explicitly():
    fx, client, _, scratch, _ = make_env()
    tr = make_trader(fx, client, scratch, PaperConfig(use_stream=False, inject="clock-skew"))
    tr.rebalance_once()
    assert client.timestamp_resyncs == 1
    assert fx.positions["ETHUSDT"] > 0
    print("PASS clock_skew_injection_handled_explicitly")


def test_fail_closed_flattens_and_halts():
    fx, client, _, scratch, _ = make_env()
    fx.positions["ETHUSDT"] = 0.05   # an open position when things go wrong
    tr = make_trader(fx, client, scratch, PaperConfig(use_stream=False, inject="raise"))
    rc = tr.run(once=True)
    assert rc == 2 and tr.halted
    assert all(u == 0 for u in fx.positions.values()), fx.positions
    kinds = [r["kind"] for r in read_jsonl(scratch / "paper_log.jsonl")]
    assert "halt" in kinds and "halted" in kinds
    # a transport exception mid-order is also fail-closed
    fx2, client2, _, scratch2, _ = make_env()
    fx2.raise_on_order = ConnectionResetError("socket died")
    tr2 = make_trader(fx2, client2, scratch2, PaperConfig(use_stream=False))
    rc2 = tr2.run(once=True)
    assert rc2 == 2
    print("PASS fail_closed_flattens_and_halts")


def test_unknown_position_at_startup_halts_unless_opted_in():
    fx, client, _, scratch, _ = make_env()
    fx.positions["SOLUSDT"] = 3.0
    tr = make_trader(fx, client, scratch, PaperConfig(use_stream=False))
    assert tr.run(once=True) == 2
    assert fx.positions["SOLUSDT"] == 0.0     # halt flattens everything
    fx.positions["SOLUSDT"] = 3.0
    tr2 = make_trader(fx, client, scratch, PaperConfig(use_stream=False, flatten_unknown=True))
    assert tr2.run(once=True) == 0
    assert fx.positions["SOLUSDT"] == 0.0 and fx.positions["ETHUSDT"] > 0
    print("PASS unknown_position_at_startup_halts_unless_opted_in")


def test_maker_mode_post_only_and_unfilled_path():
    fx, client, _, scratch, _ = make_env()
    tr = make_trader(fx, client, scratch, PaperConfig(use_stream=False, fee_mode="maker"))
    tr.rebalance_once()
    fills = [r for r in tr.costlog.records() if r["kind"] == "fill"]
    assert len(fills) == 2 and all(f["maker"] for f in fills)
    for f in fills:
        assert math.isclose(f["fee"], f["notional"] * 0.0002, rel_tol=1e-9)
    fx2, client2, _, scratch2, _ = make_env()
    fx2.maker_no_fill = True
    tr2 = make_trader(fx2, client2, scratch2, PaperConfig(use_stream=False, fee_mode="maker",
                                                           maker_wait_s=10))
    tr2.rebalance_once()
    assert tr2.stats.maker_unfilled == 2
    assert all(u == 0 for u in fx2.positions.values())
    assert not [o for o in fx2.open_orders if o["type"] == "LIMIT"], "stale limit left open"
    print("PASS maker_mode_post_only_and_unfilled_path")


def test_daily_record_has_no_pnl_and_funding_sign_checked():
    fx, client, _, scratch, _ = make_env()
    tr = make_trader(fx, client, scratch, PaperConfig(use_stream=False))
    tr.rebalance_once()
    eth = fx.positions["ETHUSDT"]
    # exchange says: long ETH paid 0.0001 * notional at a settlement
    mark = sum(fx.PRICES["ETHUSDT"]) / 2
    fx.income = [{"incomeType": "FUNDING_FEE", "income": str(-eth * mark * 0.0001),
                  "symbol": "ETHUSDT", "time": fx.now_ms() - 1000},
                 {"incomeType": "COMMISSION", "income": "-0.02", "symbol": "ETHUSDT",
                  "time": fx.now_ms() - 1000}]
    rec = tr.record_day()
    assert "pnl" not in json.dumps(rec).lower()
    assert rec["fills"] == 2 and rec["total_fees_exchange"] == 0.02
    fund = [r for r in tr.costlog.records() if r["kind"] == "funding"][0]
    assert fund["sign_ok"] and fund["expected_amount"] == funding_cashflow(eth, mark, 0.0001)
    print("PASS daily_record_has_no_pnl_and_funding_sign_checked")


def test_costlog_slippage_and_weekly_report():
    log = CL.CostLog(Path(tempfile.mkdtemp()) / "c.jsonl")
    assert CL.slippage_bps("BUY", 100.0, 100.05) > 0
    assert CL.slippage_bps("SELL", 100.0, 100.05) < 0
    log.record_fill(symbol="X", side="BUY", intended_price=100.0, fill_price=100.1,
                    qty=1.0, fee=0.05005, fee_asset="USDT", maker=False, order_type="MARKET")
    log.record_fill(symbol="X", side="SELL", intended_price=100.0, fill_price=100.0,
                    qty=2.0, fee=0.04, fee_asset="USDT", maker=True, order_type="LIMIT")
    log.record_funding(symbol="X", position_units=-1.0, mark=100.0, rate=0.0001,
                       actual_amount=0.01, ts_ms=1)
    rep = CL.weekly_report(log, since_ms=0)
    assert math.isclose(rep["taker_fee_actual"], 0.0005, rel_tol=1e-9)
    assert math.isclose(rep["maker_fee_actual"], 0.0002, rel_tol=1e-9)
    assert rep["funding_sign_mismatches"] == 0
    assert "pnl" not in json.dumps(rep).lower()
    print("PASS costlog_slippage_and_weekly_report")


def test_watchdog_flattens_on_stale_heartbeat_only():
    scratch = Path(tempfile.mkdtemp())
    hb, plog = scratch / "heartbeat", scratch / "paper_log.jsonl"
    calls = []

    def fake_flatten():
        calls.append(1)
        return {"remaining": {}, "closed": [], "cancelled": [], "errors": []}

    hb.write_text("1000.0")
    assert watchdog.check_once(hb, 120, plog, flatten=fake_flatten, now=1100.0) is None
    rec = watchdog.check_once(hb, 120, plog, flatten=fake_flatten, now=1121.0)
    assert rec and rec["kind"] == "watchdog_trigger" and len(calls) == 1
    assert read_jsonl(plog)[0]["heartbeat_age_s"] == 121.0
    print("PASS watchdog_flattens_on_stale_heartbeat_only")


def test_killswitch_standalone_flattens_everything():
    fx = FakeExchange()
    fx.positions.update({"ETHUSDT": 0.05, "BNBUSDT": -0.3})
    fx.open_orders.append({"orderId": 1, "clientOrderId": "s", "symbol": "ETHUSDT",
                           "type": "STOP_MARKET", "status": "NEW", "side": "SELL"})

    def request(method, path, params, key, secret):   # stdlib-shaped adapter
        status, _, body = fx(method, path, {**params, "timestamp": fx.now_ms(),
                                            "signature": "x"}, signed=False)
        if status != 200:
            raise RuntimeError(body)
        return body

    res = killswitch.flatten_all("k", "s", request=request)
    assert res["remaining"] == {} and not res["errors"]
    assert res["cancelled"] == ["ETHUSDT"] and len(res["closed"]) == 2
    assert all(u == 0 for u in fx.positions.values()) and not fx.open_orders
    # the kill switch shares no code with the trader/client
    src = (Path(__file__).resolve().parents[1] / "live" / "killswitch.py").read_text()
    assert "from live.client" not in src and "import requests" not in src
    src_w = (Path(__file__).resolve().parents[1] / "live" / "watchdog.py").read_text()
    assert "live.client" not in src_w and "live.trader" not in src_w
    print("PASS killswitch_standalone_flattens_everything")


def test_phase_two_is_refused():
    fx, client, _, scratch, _ = make_env()
    try:
        make_trader(fx, client, scratch, PaperConfig(use_stream=False, phase=2))
    except NotImplementedError:
        pass
    else:
        raise AssertionError("phase 2 must be refused until grid + holdout exist")
    print("PASS phase_two_is_refused")


def test_plan_deltas():
    assert reconcile.plan_deltas({"A": 1.0, "B": -2.0}, {"A": 1.0, "C": 0.5}) == {"B": 2.0, "C": 0.5}
    assert reconcile.plan_deltas({}, {}) == {}
    print("PASS plan_deltas")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
