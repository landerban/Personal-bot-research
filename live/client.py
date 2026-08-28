"""
Binance USDS-M futures TESTNET client.

TESTNET ONLY. The only hosts in this file are testnet hosts. No mainnet
endpoint exists here, not even commented out, and no code path can build
one. Keys come from environment variables and are never logged.

REST is the source of truth for positions, orders, fills, fees and funding.
The user-data WebSocket (UserDataStream) is telemetry: it makes fills
visible in real time, and every disconnect triggers a REST reconcile, so a
gap in the stream cannot become a gap in state.

Failure handling, in one place (request()):
  * 429 / 418            -> exponential backoff honouring Retry-After;
                            never hammer, never spin
  * code -1021           -> local clock drifted outside recvWindow: resync
                            the server-time offset and retry ONCE, then raise
  * filter / notional    -> FilterRejected (caller decides; never retried)
  * 5xx / network error  -> backoff and retry, bounded
  * anything else        -> ExchangeError, no retry
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable, Optional

import requests

log = logging.getLogger("live.client")

REST_BASE = "https://testnet.binancefuture.com"
# Verify against the current Binance futures testnet docs before first use;
# the testnet stream host has changed name in the past.
WS_BASE = "wss://fstream.binancefuture.com"

ENV_KEY = "BINANCE_TESTNET_API_KEY"
ENV_SECRET = "BINANCE_TESTNET_API_SECRET"
# Stage 10 2.1 names the variables without the API_ infix. Both are accepted so
# the documented names work and the already-tested ones do not break; the
# Stage 10 spelling wins when both are set.
ENV_KEY_ALIASES = ("BINANCE_TESTNET_KEY", ENV_KEY)
ENV_SECRET_ALIASES = ("BINANCE_TESTNET_SECRET", ENV_SECRET)

# Stage 10 2.2: the base URL is a switch, and the switch is asserted.
#
# This is an ALLOW-LIST, deliberately, and it is the only list in this file.
# A deny-list of production hosts would have to NAME them, which breaks the
# standing B1/B7 rule that no mainnet host string appears in live/ at all
# (test_no_mainnet_anywhere_in_live) -- and a deny-list is weaker anyway: it
# misses whatever host you forgot. Refusing everything that is not a known
# testnet host refuses every production venue without naming any of them,
# including ones that do not exist yet.
TESTNET_HOSTS = frozenset({
    "testnet.binancefuture.com", "fstream.binancefuture.com",
    "testnet.binance.vision",
})


def env_credentials() -> tuple[str, str]:
    """(key, secret) from the environment, first alias that is set. Returns
    empty strings when absent -- the caller decides whether that is fatal, and
    NOTHING here ever logs or echoes a value."""
    key = next((os.environ[n] for n in ENV_KEY_ALIASES if os.environ.get(n)), "")
    sec = next((os.environ[n] for n in ENV_SECRET_ALIASES if os.environ.get(n)), "")
    return key, sec


def assert_testnet_url(url: str, testnet: bool = True) -> str:
    """
    Stage 10 2.2, in both directions: with testnet=True the URL must be one of
    the known testnet hosts, so every other host -- production venues included
    -- is refused by exclusion; with testnet=False this client refuses
    outright, because it has no business speaking to a venue that settles real
    money.

    One assertion, at construction, before any request exists. It prevents the
    worst category of accident this project can have.
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not testnet:
        raise ValueError(
            "TestnetClient refuses testnet=False: this codebase is testnet-only "
            "(NOTES 46.8). Real-money trading is not a flag away."
        )
    if host not in TESTNET_HOSTS:
        raise ValueError(
            f"refusing host {host!r}: not a known testnet host. Expected one "
            f"of {sorted(TESTNET_HOSTS)}. Anything else -- production venues "
            f"included -- is refused here, by construction."
        )
    return url

# Binance error codes we handle by name. Anything else is a hard error.
CODE_TIMESTAMP = -1021          # timestamp outside recvWindow
CODE_FILTER = -1013             # LOT_SIZE / PRICE_FILTER / etc.
CODE_PRECISION = -1111          # precision over the maximum defined
CODE_MIN_NOTIONAL = -4164       # notional below MIN_NOTIONAL (non reduce-only)
CODE_POST_ONLY_REJECT = -5022   # GTX order would have taken liquidity
FILTER_CODES = {CODE_FILTER, CODE_PRECISION, CODE_MIN_NOTIONAL, CODE_POST_ONLY_REJECT}

# Transport: (method, path, params, signed) -> (status, headers, body).
# Injected in tests; the default speaks HTTPS to the testnet.
Transport = Callable[[str, str, dict, bool], tuple[int, dict, Any]]


class ExchangeError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None, status: int | None = None):
        super().__init__(f"{msg} (code={code}, http={status})")
        self.code, self.status = code, status


class RateLimited(ExchangeError):
    pass


class TimestampRejected(ExchangeError):
    pass


class FilterRejected(ExchangeError):
    """Order rejected by an exchange filter (quantisation, notional, post-only)."""


class NetworkError(ExchangeError):
    pass


def sign(secret: str, query: str) -> str:
    """HMAC-SHA256 of the query string, hex. Same scheme on all Binance APIs."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- filters

@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    step_size: Decimal
    tick_size: Decimal
    min_notional: Decimal
    min_qty: Decimal


def quantize_qty(qty: float, step: Decimal) -> Decimal:
    """Floor |qty| to the LOT_SIZE step (never round up into a bigger order)."""
    d = Decimal(str(abs(qty)))
    q = (d / step).to_integral_value(rounding=ROUND_DOWN) * step
    return q.quantize(step) if step < 1 else q


def quantize_price(price: float, tick: Decimal, side: str) -> Decimal:
    """Round a stop/limit price to the tick, away from the position for
    stops (BUY stops round up, SELL stops round down) so it never lands
    inside the tick on the wrong side."""
    d = Decimal(str(price))
    n = d / tick
    n = n.to_integral_value(rounding="ROUND_UP" if side == "BUY" else ROUND_DOWN)
    return (n * tick).quantize(tick) if tick < 1 else n * tick


def parse_filters(info: dict) -> dict[str, SymbolFilters]:
    out = {}
    for s in info.get("symbols", []):
        step = tick = notional = min_qty = None
        for f in s.get("filters", []):
            t = f.get("filterType")
            if t == "LOT_SIZE":
                step, min_qty = Decimal(f["stepSize"]), Decimal(f["minQty"])
            elif t == "PRICE_FILTER":
                tick = Decimal(f["tickSize"])
            elif t == "MIN_NOTIONAL":
                notional = Decimal(f["notional"])
        if step is None or tick is None or notional is None:
            continue  # a symbol without full filters is not tradeable by us
        out[s["symbol"]] = SymbolFilters(
            s["symbol"], step, tick, notional, min_qty or step
        )
    return out


# ----------------------------------------------------------------- client

class TestnetClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        recv_window_ms: int = 5000,
        max_retries: int = 5,
        base_url: str = REST_BASE,
        testnet: bool = True,
    ):
        # Stage 10 2.2: assert the venue BEFORE anything else exists.
        self.base_url = assert_testnet_url(base_url, testnet)
        self.testnet = True
        env_key, env_secret = env_credentials()
        self._key = api_key if api_key is not None else env_key
        self._secret = api_secret if api_secret is not None else env_secret
        self._transport = transport or self._http_transport
        self._sleep = sleeper
        self._clock = clock
        self._recv_window = recv_window_ms
        self._max_retries = max_retries
        self._time_offset_ms = 0
        self._filters: dict[str, SymbolFilters] | None = None
        self._session: requests.Session | None = None
        self.used_weight_1m = 0
        self.backoffs: list[float] = []      # every sleep taken for a limit/5xx
        self.timestamp_resyncs = 0
        # Failure injection (B5, row 9): pretend the local clock is skewed
        # instead of touching the system clock.
        self.inject_clock_skew_ms = 0

    # -- transport --------------------------------------------------------

    def _http_transport(self, method: str, path: str, params: dict, signed: bool):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "xsmom-paper/0.1"})
        headers = {"X-MBX-APIKEY": self._key} if self._key else {}
        try:
            r = self._session.request(
                method, self.base_url + path, params=params, headers=headers, timeout=15
            )
        except requests.RequestException as e:
            raise NetworkError(f"network: {type(e).__name__}: {e}") from None
        try:
            body = r.json()
        except ValueError:
            body = {"code": None, "msg": r.text[:200]}
        return r.status_code, dict(r.headers), body

    def _timestamp(self) -> int:
        return int(self._clock() * 1000) + self._time_offset_ms + self.inject_clock_skew_ms

    def sync_time(self) -> int:
        server = int(self.request("GET", "/fapi/v1/time")["serverTime"])
        self._time_offset_ms = server - int(self._clock() * 1000)
        return self._time_offset_ms

    def request(self, method: str, path: str, params: dict | None = None,
                signed: bool = False) -> Any:
        resynced = False
        attempt = 0
        while True:
            p = dict(params or {})
            if signed:
                if not self._key or not self._secret:
                    raise ExchangeError(f"missing {ENV_KEY}/{ENV_SECRET} in environment")
                p["timestamp"] = self._timestamp()
                p["recvWindow"] = self._recv_window
                p["signature"] = sign(self._secret, urllib.parse.urlencode(p))
            try:
                status, headers, body = self._transport(method, path, p, signed)
            except NetworkError as e:
                if attempt >= self._max_retries:
                    raise
                self._backoff(attempt, None, str(e))
                attempt += 1
                continue

            w = headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m")
            if w is not None:
                try:
                    self.used_weight_1m = int(w)
                except ValueError:
                    pass

            if status == 200:
                return body

            code = body.get("code") if isinstance(body, dict) else None
            msg = body.get("msg", "") if isinstance(body, dict) else str(body)[:200]

            if status in (429, 418):
                if attempt >= self._max_retries:
                    raise RateLimited(f"rate limited: {msg}", code, status)
                ra = headers.get("Retry-After") or headers.get("retry-after")
                self._backoff(attempt, float(ra) if ra else None, "rate limit")
                attempt += 1
                continue
            if code == CODE_TIMESTAMP:
                # Clock drift. Resync once; a second rejection is a real fault.
                if resynced:
                    raise TimestampRejected(msg, code, status)
                log.warning("timestamp rejected (-1021); resyncing server time")
                self.timestamp_resyncs += 1
                self.inject_clock_skew_ms = 0  # an injected skew is 'fixed' by resync
                self.sync_time()
                resynced = True
                continue
            if code in FILTER_CODES:
                raise FilterRejected(msg, code, status)
            if status >= 500:
                if attempt >= self._max_retries:
                    raise ExchangeError(f"server error: {msg}", code, status)
                self._backoff(attempt, None, "5xx")
                attempt += 1
                continue
            raise ExchangeError(msg, code, status)

    def _backoff(self, attempt: int, retry_after: float | None, why: str) -> None:
        # 1, 2, 4, 8, 16 s (+ jitter-free for determinism), or what the
        # exchange asked for, whichever is longer. Never shorter.
        delay = max(2.0 ** attempt, retry_after or 0.0)
        log.warning("backoff %.1fs (%s, attempt %d)", delay, why, attempt + 1)
        self.backoffs.append(delay)
        self._sleep(delay)

    # -- public market data -----------------------------------------------

    def server_time(self) -> int:
        return int(self.request("GET", "/fapi/v1/time")["serverTime"])

    def filters(self, refresh: bool = False) -> dict[str, SymbolFilters]:
        if self._filters is None or refresh:
            self._filters = parse_filters(self.request("GET", "/fapi/v1/exchangeInfo"))
        return self._filters

    def book(self, symbol: str) -> tuple[float, float]:
        b = self.request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        return float(b["bidPrice"]), float(b["askPrice"])

    def mid(self, symbol: str) -> float:
        bid, ask = self.book(symbol)
        return (bid + ask) / 2.0

    def klines(self, symbol: str, interval: str = "1d", limit: int = 61) -> list[tuple]:
        """[(open_time, open, high, low, close), ...] oldest first, only
        COMPLETED bars (the last row from the API is the forming bar)."""
        rows = self.request("GET", "/fapi/v1/klines",
                            {"symbol": symbol, "interval": interval, "limit": limit + 1})
        now = self._timestamp()
        out = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
               for r in rows if int(r[6]) <= now]
        return out[-limit:]

    def funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        rows = self.request("GET", "/fapi/v1/fundingRate",
                            {"symbol": symbol, "startTime": start_ms,
                             "endTime": end_ms, "limit": 1000})
        return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]

    def mark_price(self, symbol: str) -> float:
        return float(self.request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    # -- account (signed) ---------------------------------------------------

    def dual_side_position(self) -> bool:
        return bool(self.request("GET", "/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"])

    def equity(self) -> float:
        acct = self.request("GET", "/fapi/v2/account", signed=True)
        return float(acct["totalMarginBalance"])

    def positions(self) -> dict[str, float]:
        """{symbol: signed units} for every non-zero position. Exchange truth."""
        rows = self.request("GET", "/fapi/v2/positionRisk", signed=True)
        out = {}
        for r in rows:
            amt = float(r["positionAmt"])
            if amt != 0.0:
                if r.get("positionSide", "BOTH") != "BOTH":
                    raise ExchangeError("hedge-mode position found; one-way mode required")
                out[r["symbol"]] = amt
        return out

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        p = {"symbol": symbol} if symbol else {}
        return self.request("GET", "/fapi/v1/openOrders", p, signed=True)

    def place_order(self, **params) -> dict:
        params.setdefault("newOrderRespType", "RESULT")
        return self.request("POST", "/fapi/v1/order", params, signed=True)

    def get_order(self, symbol: str, client_order_id: str) -> dict:
        return self.request("GET", "/fapi/v1/order",
                            {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True)

    def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        return self.request("DELETE", "/fapi/v1/order",
                            {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True)

    def cancel_all(self, symbol: str) -> dict:
        return self.request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def user_trades(self, symbol: str, order_id: int | None = None,
                    start_ms: int | None = None) -> list[dict]:
        p: dict = {"symbol": symbol, "limit": 1000}
        if order_id is not None:
            p["orderId"] = order_id
        if start_ms is not None:
            p["startTime"] = start_ms
        return self.request("GET", "/fapi/v1/userTrades", p, signed=True)

    def income(self, income_type: str, start_ms: int, end_ms: int) -> list[dict]:
        return self.request("GET", "/fapi/v1/income",
                            {"incomeType": income_type, "startTime": start_ms,
                             "endTime": end_ms, "limit": 1000}, signed=True)

    # -- user data stream (listen key) -------------------------------------

    def new_listen_key(self) -> str:
        return self.request("POST", "/fapi/v1/listenKey")["listenKey"]

    def keepalive_listen_key(self) -> None:
        self.request("PUT", "/fapi/v1/listenKey")

    def close_listen_key(self) -> None:
        self.request("DELETE", "/fapi/v1/listenKey")


# --------------------------------------------------------- websocket stream

class UserDataStream(threading.Thread):
    """
    Optional real-time fill telemetry over the user-data WebSocket.

    Reconnects with bounded backoff on any close, refreshes the listen key
    every 30 minutes, and calls `on_reconnect` after each re-establishment
    so the owner can REST-reconcile — the stream is never the state.
    Uses websocket-client (already installed); imported lazily so nothing
    else in live/ depends on it.
    """

    def __init__(self, client: TestnetClient, on_event: Callable[[dict], None],
                 on_reconnect: Callable[[], None] | None = None,
                 ws_base: str = WS_BASE):
        super().__init__(daemon=True, name="user-data-stream")
        self._client = client
        self._on_event = on_event
        self._on_reconnect = on_reconnect
        self._ws_base = ws_base
        self._stop = threading.Event()
        self._app = None
        self.reconnects = 0
        self.last_message_ms = 0

    def run(self) -> None:
        import json

        import websocket  # websocket-client

        backoff = 1.0
        first = True
        while not self._stop.is_set():
            try:
                key = self._client.new_listen_key()
                url = f"{self._ws_base}/ws/{key}"
                last_keepalive = time.time()

                def on_message(_ws, message):
                    self.last_message_ms = int(time.time() * 1000)
                    try:
                        self._on_event(json.loads(message))
                    except Exception:  # a bad callback must not kill the stream
                        log.exception("user-data event handler failed")

                self._app = websocket.WebSocketApp(url, on_message=on_message)
                if not first:
                    self.reconnects += 1
                    if self._on_reconnect:
                        self._on_reconnect()
                first = False
                backoff = 1.0
                # run_forever returns on close/error; ping keeps NAT alive
                self._app.run_forever(ping_interval=20, ping_timeout=10)
                if time.time() - last_keepalive > 1800:
                    self._client.keepalive_listen_key()
            except Exception as e:
                log.warning("user-data stream error: %s", e)
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def kill_connection(self) -> None:
        """Failure injection (B5, row 8): drop the socket; run() reconnects."""
        if self._app is not None:
            try:
                self._app.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        self.kill_connection()
