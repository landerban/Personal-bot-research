"""
Stage 10 2.4 / 46.3: a point-in-time view built from LIVE testnet data.

The live decision path must run the SAME strategy the backtester validated.
The strongest available form of that guarantee is not "two implementations
that agree" -- it is "one implementation, run twice on different data
sources". So this module does not reimplement anything: it fetches bars,
funding and filters from the testnet REST API, loads them into an in-memory
`PointInTimeStore` using the same schema and the same insert paths the
research store uses, and hands back a real `PITView`.

`backtest.weights.compute_target_weights` then runs unmodified on it.

WHY THIS SHAPE, and what it costs
---------------------------------
STAGE10 3 describes running the backtester separately after the live decision
and comparing the two. Taken literally against a live path that already calls
`compute_target_weights`, that comparison would be a function compared with
itself -- the Stage 2e vacuity trap, a check that passes because it cannot
fail.

So the identity guarantee is moved into construction (same function, same
data schema, same time gate), and the shadow check in `live/shadow.py`
compares the things that CAN still differ:

  * the decision recomputed from an INDEPENDENT re-fetch of the bars
    (catches staleness, partial pages, a mid-flight listing change)
  * the target book against the book actually FILLED
    (catches execution divergence -- also STAGE10 4.1)

Those are real comparisons with real failure modes. The vacuous one is not
performed and is not claimed.

The time gate is preserved end to end: bars are inserted with their true
`close_time`, and the view is opened at an `as_of` that only admits closed
bars, exactly as `PITView` does in research.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from live.client import TestnetClient
from pitdata.store import PITView, PointInTimeStore

log = logging.getLogger("live.pitfeed")

DAY_MS = 86_400_000
# Enough history for beta_window/vol_window (60) plus the momentum lookback
# plus slack. Testnet serves ~90 daily bars per symbol.
DEFAULT_BARS = 90
# Funding must be present within FUNDING_PRESENCE_WINDOW_MS of as_of for a
# symbol to be a candidate (weights.py), so fetch a window comfortably past it.
FUNDING_LOOKBACK_MS = 14 * DAY_MS


@dataclass
class FeedSnapshot:
    """Exactly what was fetched, so a decision can be replayed or audited."""
    as_of: int
    fetched_at_ms: int
    symbols: tuple[str, ...]
    bars_per_symbol: dict[str, int] = field(default_factory=dict)
    last_close_time: dict[str, int] = field(default_factory=dict)
    funding_rows: dict[str, int] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    def summary(self) -> dict:
        return {
            "as_of": self.as_of, "fetched_at_ms": self.fetched_at_ms,
            "n_symbols": len(self.symbols), "symbols": list(self.symbols),
            "bars_per_symbol": self.bars_per_symbol,
            "last_close_time": self.last_close_time,
            "funding_rows": self.funding_rows,
            "missing": list(self.missing),
        }


class LiveFeed:
    """Builds a throwaway in-memory PIT store per decision.

    One store per decision is deliberate: nothing carries over between days,
    so a stale row cannot survive into tomorrow's book, and the monotonic
    as_of guard starts clean.
    """

    def __init__(self, client: TestnetClient, clock=time.time):
        self.c = client
        self._clock = clock
        self.store: PointInTimeStore | None = None

    # -- fetch ------------------------------------------------------------

    def _klines(self, symbol: str, limit: int) -> list[tuple]:
        rows = self.c.request("GET", "/fapi/v1/klines",
                              {"symbol": symbol, "interval": "1d", "limit": limit})
        # Binance kline row: [openTime, o, h, l, c, vol, closeTime, quoteVol,
        #                     trades, takerBuyVol, takerBuyQuote, ignore]
        return [
            (int(r[0]), int(r[6]), float(r[1]), float(r[2]), float(r[3]),
             float(r[4]), float(r[5]), float(r[7]), int(r[8]))
            for r in rows
        ]

    def _funding(self, symbol: str, since_ms: int) -> list[tuple[int, float]]:
        rows = self.c.request("GET", "/fapi/v1/fundingRate",
                              {"symbol": symbol, "startTime": since_ms, "limit": 1000})
        return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]

    def _filters(self) -> list[dict]:
        info = self.c.request("GET", "/fapi/v1/exchangeInfo")
        out = []
        for s in info.get("symbols", []):
            mn = step = tick = None
            for f in s.get("filters", []):
                if f.get("filterType") == "MIN_NOTIONAL":
                    mn = float(f["notional"])
                elif f.get("filterType") == "LOT_SIZE":
                    step = float(f["stepSize"])
                elif f.get("filterType") == "PRICE_FILTER":
                    tick = float(f["tickSize"])
            out.append({"symbol": s["symbol"], "status": s.get("status", "UNKNOWN"),
                        "min_notional": mn, "step_size": step, "tick_size": tick})
        return out

    # -- build ------------------------------------------------------------

    def build(self, symbols: list[str], as_of: int | None = None,
              bars: int = DEFAULT_BARS,
              volume_reference: dict[str, float] | None = None,
              ) -> tuple[PITView, FeedSnapshot]:
        """Fetch `symbols` and return (view, snapshot).

        `as_of` defaults to now; only bars whose close_time <= as_of are
        visible through the view, which is the same gate research runs under.
        A symbol that returns no bars is reported in `snapshot.missing` and is
        simply absent from the store -- never imputed.

        `volume_reference` (NOTES 55.1) overwrites each bar's `quote_volume`
        with the symbol's PRODUCTION median quote volume. This is not
        cosmetic: `compute_target_weights` ranks the `max_liquidity_rank` cap
        off `quote_volume` read from THIS store, so without the override the
        cap re-ranks by testnet's synthetic volume and reinstates exactly the
        junk the shortlist just removed -- which is why the first Part A
        attempt still produced 0% book formation.

        The result is a deliberate hybrid: LIVE prices, PRODUCTION liquidity.
        That is the correct pairing, because price drives the returns while
        `quote_volume` is only ever read as a liquidity measure, and the
        liquidity the rule means is the real market's, not the sandbox's.

        A symbol the reference cannot price gets volume 0, so it fails the
        `min_quote_volume` filter and is not traded. What cannot be ranked by
        the rule is not admitted by it.
        """
        fetched_at = int(self._clock() * 1000)
        as_of = int(as_of if as_of is not None else fetched_at)

        if self.store is not None:
            self.store.close()
        self.store = PointInTimeStore(":memory:")

        snap = FeedSnapshot(as_of=as_of, fetched_at_ms=fetched_at,
                            symbols=tuple(symbols))
        missing = []
        for sym in symbols:
            rows = self._klines(sym, bars)
            if not rows:
                missing.append(sym)
                continue
            if volume_reference is not None:
                ref = float(volume_reference.get(sym, 0.0))
                # row: (open_time, close_time, o, h, l, c, volume, quote_volume, trades)
                rows = [r[:7] + (ref,) + r[8:] for r in rows]
            self.store.insert_klines(sym, "1d", rows)
            snap.bars_per_symbol[sym] = len(rows)
            snap.last_close_time[sym] = max(r[1] for r in rows)
            frows = self._funding(sym, fetched_at - FUNDING_LOOKBACK_MS)
            if frows:
                self.store.insert_funding(sym, frows)
            snap.funding_rows[sym] = len(frows)
        snap.missing = tuple(missing)

        self.store.insert_filters(fetched_at, self._filters())
        view = self.store.view_as_of(as_of)
        log.info("live feed: %d symbols, as_of=%d, %d missing",
                 len(symbols), as_of, len(missing))
        return view, snap

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None
