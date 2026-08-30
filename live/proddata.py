"""
Stage 16 Part B: the PRODUCTION MARKET-DATA client. READ-ONLY. UNSIGNED.

This is the only module in the codebase permitted to name a production host,
and NOTES 56.1 records why that narrowing is safe rather than assuming it:

  * it holds no credential, takes none, and imports no crypto primitive --
    there is no code path here that can produce a signature
  * it issues GET and nothing else; the transport refuses any other verb
  * it reaches an ALLOW-LIST of public market-data endpoints and nothing else

The standing rule (B1/B7) was "no mainnet host anywhere in live/", and its
purpose was that no code path can reach a venue that settles real money. A
client that cannot sign cannot place an order, so it cannot. The trading
modules -- client.py, killswitch.py, trader.py, phase2.py -- remain bound by
the original absolute rule, and the test enforces that split.

WHY THIS EXISTS
---------------
§55.9: testnet's ~89 days of synthetic price history cannot identify 60-day
betas for the names momentum selects, so the `unhedgeable_beta` guard -- doing
exactly its job -- blocked book formation on 12 of 12 days. The screen is not
broken; the sandbox data is. Real market data is the market the research
measured, and reading it risks nothing.

WHAT IT MAY NOT DO
------------------
Place, cancel or modify anything; read account state; hold a key; sign. If a
future need seems to require any of those, it belongs in a different module
behind the holdout gate, not here.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger("live.proddata")

# The public market-data host. Named here and nowhere else in the codebase.
PROD_BASE = "https://fapi.binance.com"

# Allow-list. A path not on this list is refused before any request is built,
# so the blast radius of a typo or a future edit is a raised exception rather
# than an unintended call.
ALLOWED_PATHS = frozenset({
    "/fapi/v1/klines",
    "/fapi/v1/exchangeInfo",
    "/fapi/v1/fundingRate",
    "/fapi/v1/premiumIndex",
    "/fapi/v1/ticker/24hr",
    "/fapi/v1/ticker/bookTicker",
    "/fapi/v1/time",
})

DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 4


class ProdDataError(RuntimeError):
    pass


class ReadOnlyViolation(RuntimeError):
    """Raised when anything asks this client to do more than read."""


class ProdDataClient:
    """GET-only, unsigned, allow-listed. Constructs no credential of any kind.

    It deliberately takes no `api_key`/`api_secret` argument: a caller cannot
    pass one even by mistake, and a reader can see at the signature that this
    object has no authority.
    """

    def __init__(self, base_url: str = PROD_BASE, timeout: float = DEFAULT_TIMEOUT,
                 sleeper=time.sleep):
        if not base_url.startswith("https://"):
            raise ValueError(f"refusing a non-TLS base url: {base_url!r}")
        self.base_url = base_url
        self.timeout = timeout
        self._sleep = sleeper
        self.requests_made = 0

    # -- the single request path -------------------------------------------

    def get(self, path: str, params: dict | None = None):
        """The ONLY way out of this class. GET, allow-listed, unsigned."""
        if path not in ALLOWED_PATHS:
            raise ReadOnlyViolation(
                f"path {path!r} is not on the market-data allow-list. This "
                f"client reads public data and does nothing else.")
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(
                    url, method="GET",
                    headers={"User-Agent": "xsmom-readonly/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.requests_made += 1
                    return json.loads(r.read().decode())
            except Exception as e:                      # network / 4xx / 5xx
                last = e
                if attempt == MAX_RETRIES - 1:
                    break
                self._sleep(min(2.0 ** attempt, 8.0))
        raise ProdDataError(f"GET {path} failed after {MAX_RETRIES}: {last}")

    # -- explicit refusals --------------------------------------------------
    #
    # These exist so that an accidental call fails LOUDLY and legibly instead
    # of raising AttributeError, and so the refusal is visible in the source
    # to anyone auditing what this client can do.

    def post(self, *a, **k):
        raise ReadOnlyViolation(
            "this client cannot POST. Production trading is forbidden and "
            "holdout-gated (NOTES 49.3 / 56.1).")

    place_order = cancel_order = cancel_all = post

    def signed(self, *a, **k):
        raise ReadOnlyViolation(
            "this client cannot sign: it holds no credential and imports no "
            "HMAC primitive. There is no signing path to enable.")

    # -- the market-data surface -------------------------------------------

    def server_time(self) -> int:
        return int(self.get("/fapi/v1/time")["serverTime"])

    def exchange_info(self) -> dict:
        return self.get("/fapi/v1/exchangeInfo")

    def klines(self, symbol: str, interval: str = "1d", limit: int = 100,
               start_ms: int | None = None, end_ms: int | None = None) -> list:
        p: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            p["startTime"] = start_ms
        if end_ms is not None:
            p["endTime"] = end_ms
        return self.get("/fapi/v1/klines", p)

    def funding_rates(self, symbol: str, start_ms: int | None = None,
                      limit: int = 1000) -> list:
        p: dict = {"symbol": symbol, "limit": limit}
        if start_ms is not None:
            p["startTime"] = start_ms
        return self.get("/fapi/v1/fundingRate", p)

    def premium_index(self, symbol: str) -> dict:
        return self.get("/fapi/v1/premiumIndex", {"symbol": symbol})

    def ticker_24hr(self, symbol: str | None = None):
        return self.get("/fapi/v1/ticker/24hr",
                        {"symbol": symbol} if symbol else None)

    def book_ticker(self, symbol: str | None = None):
        """Best bid/ask. The spread evidence Stage 16 C.2 collects."""
        return self.get("/fapi/v1/ticker/bookTicker",
                        {"symbol": symbol} if symbol else None)


def assert_execution_is_simulated(paper_feed: str, execution: str) -> None:
    """NOTES 56.1's combination rule, as a startup refusal.

    `paper_feed=production` may pair ONLY with `execution=simulated`. Live
    execution against production data is refused here rather than warned
    about, because the whole safety argument for reading production data is
    that nothing can act on it.
    """
    if paper_feed == "production" and execution != "simulated":
        raise ReadOnlyViolation(
            f"refusing paper_feed=production with execution={execution!r}. "
            f"Production data is permitted ONLY with simulated execution "
            f"(NOTES 56.1). Live execution stays on testnet and real-money "
            f"trading stays gated on the holdout decision.")
