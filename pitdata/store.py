"""
Point-in-time data store.

DESIGN INVARIANT
----------------
It must be *impossible* for backtest code to read a data point that was not
knowable at the simulated timestamp. This is enforced structurally, not by
convention:

  - All reads go through PITView, which carries an `as_of` timestamp.
  - Every query injects `close_time <= as_of` (or `funding_time <= as_of`).
  - PITView exposes no connection, cursor, or raw-SQL escape hatch.
  - Time may only move forward within a run.

WHY close_time AND NOT open_time
--------------------------------
A daily bar stamped open_time=2024-01-01T00:00 is not knowable until
2024-01-02T00:00. Filtering on open_time is the single most common lookahead
bug in backtesting: it leaks a full bar of future information, which for a
daily strategy is exactly the horizon you are trying to predict. We filter on
close_time, so a partially-formed bar is invisible until it completes.

FUNDING
-------
Binance settles funding at 00:00/08:00/16:00 UTC. The rate for a settlement is
known *at* that settlement, so `funding_time <= as_of` is the correct gate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

# Binance switched some 2025+ dumps from millisecond to microsecond stamps.
# Anything past this bound is microseconds and gets normalised on ingest.
_MICROSECOND_THRESHOLD = 1_000_000_000_000_000  # 1e15

# Smallest position as a fraction of the average position. This MUST equal
# the lower bound of the rank-weight band in backtest/weights.py (spec
# section 2.3.4: [0.5x, 1.5x] of leg average). Vol scaling is already inside
# the gross leverage passed to tradeable_universe, so it must not be counted
# here again -- an earlier 0.25 did exactly that and halved the floor.
# Changing one without the other silently breaks MIN_NOTIONAL enforcement;
# Stage 2 Test 15 asserts the three places that hold this number agree.
MIN_WEIGHT_FRACTION = 0.5


_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _interval_ms(interval: str) -> int:
    try:
        return _INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(f"unsupported interval: {interval}") from None


def normalise_timestamp(ts: int) -> int:
    """Return milliseconds, coercing microsecond stamps if present."""
    ts = int(ts)
    if ts >= _MICROSECOND_THRESHOLD:
        return ts // 1000
    return ts


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS klines (
    symbol        TEXT    NOT NULL,
    interval      TEXT    NOT NULL,
    open_time     INTEGER NOT NULL,   -- ms, bar start
    close_time    INTEGER NOT NULL,   -- ms, bar end == knowable-at time
    open          REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL,
    close         REAL    NOT NULL,
    volume        REAL    NOT NULL,
    quote_volume  REAL    NOT NULL,
    trades        INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_close ON klines (interval, close_time, symbol);

CREATE TABLE IF NOT EXISTS funding (
    symbol        TEXT    NOT NULL,
    funding_time  INTEGER NOT NULL,   -- ms, settlement == knowable-at time
    funding_rate  REAL    NOT NULL,
    PRIMARY KEY (symbol, funding_time)
);
CREATE INDEX IF NOT EXISTS idx_funding_time ON funding (funding_time, symbol);

-- Exchange filters. Binance publishes only CURRENT filters, so historical
-- values are unknown. snapshot_time records when we observed them; the
-- backtest treats the earliest snapshot as applying to all prior dates and
-- this approximation is reported by audit_filter_coverage().
CREATE TABLE IF NOT EXISTS symbol_filters (
    symbol         TEXT    NOT NULL,
    snapshot_time  INTEGER NOT NULL,
    status         TEXT    NOT NULL,
    min_notional   REAL,
    step_size      REAL,
    tick_size      REAL,
    PRIMARY KEY (symbol, snapshot_time)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    source        TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    period        TEXT    NOT NULL,
    rows          INTEGER NOT NULL,
    ingested_at   INTEGER NOT NULL,
    PRIMARY KEY (source, symbol, period)
);
"""


class LookaheadError(RuntimeError):
    """Raised when code attempts to read data ahead of the simulated clock."""


@dataclass(frozen=True)
class Bar:
    symbol: str
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int


class PITView:
    """
    A read-only window onto the store, frozen at `as_of` (ms).

    Deliberately exposes no connection or raw-query method. Every accessor
    below hard-codes its time gate; there is no code path that reaches the
    database without one.
    """

    __slots__ = ("_conn", "_as_of", "_cache")

    def __init__(self, conn: sqlite3.Connection, as_of: int, cache: dict | None = None):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_as_of", int(as_of))
        object.__setattr__(self, "_cache", cache if cache is not None else {})

    @property
    def as_of(self) -> int:
        return self._as_of

    def klines(
        self, symbol: str, interval: str = "1d", limit: int = 100
    ) -> list[Bar]:
        """Most recent `limit` completed bars at or before as_of, oldest first."""
        rows = self._conn.execute(
            """
            SELECT symbol, open_time, close_time, open, high, low, close,
                   volume, quote_volume, trades
              FROM klines
             WHERE symbol = ? AND interval = ? AND close_time <= ?
             ORDER BY close_time DESC
             LIMIT ?
            """,
            (symbol, interval, self._as_of, limit),
        ).fetchall()
        return [Bar(*r) for r in reversed(rows)]

    def latest_close(self, symbol: str, interval: str = "1d") -> float | None:
        row = self._conn.execute(
            """
            SELECT close FROM klines
             WHERE symbol = ? AND interval = ? AND close_time <= ?
             ORDER BY close_time DESC LIMIT 1
            """,
            (symbol, interval, self._as_of),
        ).fetchone()
        return row[0] if row else None

    def trailing_return(
        self, symbol: str, lookback: int, skip: int = 0, interval: str = "1d"
    ) -> float | None:
        """
        Return over `lookback` bars ending `skip` bars before as_of.

        skip>0 implements the short-term-reversal guard: momentum is measured
        on [t-lookback-skip, t-skip] rather than through to the present bar.
        """
        need = lookback + skip + 1
        bars = self.klines(symbol, interval=interval, limit=need)
        if len(bars) < need:
            return None
        end = bars[-1 - skip]
        start = bars[-1 - skip - lookback]
        if start.close <= 0:
            return None
        return end.close / start.close - 1.0

    def realised_vol(
        self, symbol: str, window: int = 30, interval: str = "1d"
    ) -> float | None:
        """Stdev of log returns over `window` bars. Not annualised."""
        import math

        bars = self.klines(symbol, interval=interval, limit=window + 1)
        if len(bars) < window + 1:
            return None
        rets = []
        for a, b in zip(bars[:-1], bars[1:]):
            if a.close > 0 and b.close > 0:
                rets.append(math.log(b.close / a.close))
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var)

    def funding(self, symbol: str, since: int | None = None) -> list[tuple[int, float]]:
        """(funding_time, rate) settlements at or before as_of."""
        if since is None:
            rows = self._conn.execute(
                """
                SELECT funding_time, funding_rate FROM funding
                 WHERE symbol = ? AND funding_time <= ?
                 ORDER BY funding_time
                """,
                (symbol, self._as_of),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT funding_time, funding_rate FROM funding
                 WHERE symbol = ? AND funding_time <= ? AND funding_time >= ?
                 ORDER BY funding_time
                """,
                (symbol, self._as_of, int(since)),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def universe(
        self,
        min_quote_volume: float,
        lookback_days: int = 30,
        min_history_days: int = 60,
        interval: str = "1d",
    ) -> list[str]:
        """
        Point-in-time tradeable universe.

        A symbol qualifies at as_of when it has at least `min_history_days` of
        completed bars (so the momentum lookback is computable without
        backfill) and its median daily quote volume over the trailing
        `lookback_days` clears `min_quote_volume`.

        Median, not mean: a single listing-day volume spike should not
        promote an otherwise illiquid symbol into the universe.

        Survivorship: membership derives from bars that existed at as_of, so
        symbols later delisted are correctly included on dates when they
        traded, provided the delisted symbol's history was ingested.
        """
        # Scan only the trailing window. `close_time <= as_of` alone scans all
        # accumulated history and gets slower as the backtest progresses, which
        # is the worst possible shape: the run decelerates as it goes.
        #
        # The history-length test uses a cached per-symbol first-bar time.
        # That is not lookahead: a symbol's first bar is in the past by
        # construction, and symbols whose first bar postdates as_of are
        # excluded. Caveat: for a symbol with gaps in *distant* history,
        # elapsed-time and bar-count differ slightly. Gaps inside the recent
        # window are still caught by the len(vols) test below.
        bar_ms = _interval_ms(interval)
        window_start = self._as_of - lookback_days * bar_ms
        # N bars span (N-1) intervals between first close and last close, not
        # N. Using min_history_days directly here silently demands one extra
        # bar and quietly shrinks the universe.
        history_cutoff = self._as_of - (min_history_days - 1) * bar_ms

        rows = self._conn.execute(
            """
            SELECT symbol, quote_volume FROM klines
             WHERE interval = ? AND close_time <= ? AND close_time > ?
            """,
            (interval, self._as_of, window_start),
        ).fetchall()

        first_seen = self._first_seen(interval)

        by_symbol: dict[str, list[float]] = {}
        for sym, qv in rows:
            by_symbol.setdefault(sym, []).append(qv)

        qualified = []
        for sym, vols in by_symbol.items():
            if len(vols) < lookback_days:
                continue
            fs = first_seen.get(sym)
            if fs is None or fs > history_cutoff:
                continue
            vols.sort()
            n = len(vols)
            median = vols[n // 2] if n % 2 else (vols[n // 2 - 1] + vols[n // 2]) / 2
            if median >= min_quote_volume:
                qualified.append(sym)
        return sorted(qualified)

    def _first_seen(self, interval: str) -> dict[str, int]:
        """Per-symbol first bar close_time. Cache is owned by the store."""
        cache = self._cache
        if interval not in cache:
            cache[interval] = {
                r[0]: r[1]
                for r in self._conn.execute(
                    "SELECT symbol, MIN(close_time) FROM klines "
                    "WHERE interval = ? GROUP BY symbol",
                    (interval,),
                ).fetchall()
            }
        return cache[interval]

    def min_notional(self, symbol: str) -> float | None:
        """
        Earliest observed MIN_NOTIONAL for `symbol`.

        NOT point-in-time: Binance does not publish historical filters. See
        audit_filter_coverage() for the honest statement of this gap.
        """
        row = self._conn.execute(
            """
            SELECT min_notional FROM symbol_filters
             WHERE symbol = ? AND min_notional IS NOT NULL
             ORDER BY snapshot_time ASC LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        return row[0] if row else None

    def tradeable_universe(
        self,
        capital: float,
        gross_leverage: float,
        n_positions: int,
        min_quote_volume: float,
        min_weight_fraction: float = MIN_WEIGHT_FRACTION,
        **kwargs,
    ) -> list[str]:
        """
        Universe filtered to symbols whose smallest possible position still
        clears MIN_NOTIONAL.

        `min_weight_fraction` is the smallest position as a fraction of the
        average: the lower bound of the section 2.3.4 rank-weight band
        (0.5). It must equal that band's lower bound. Vol scaling is already
        inside `gross_leverage` and must NOT be counted here again.
        Smallest position = min_weight_fraction * gross_leverage * capital
        / n_positions; the relevant gross_leverage is the *realised* one.
        """
        avg_position = gross_leverage * capital / n_positions
        smallest = avg_position * min_weight_fraction
        out = []
        for sym in self.universe(min_quote_volume, **kwargs):
            mn = self.min_notional(sym)
            if mn is None or smallest >= mn:
                out.append(sym)
        return out


class PointInTimeStore:
    """Owns the connection. Hands out PITViews; never the connection itself."""

    def __init__(self, path: str | Path, read_only: bool = False):
        self.path = str(path)
        if read_only:
            self._conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.executescript(SCHEMA)
        self._last_as_of: int | None = None
        self._cache: dict = {}

    # ---- writes (ingest only; never called from backtest code) ----

    def insert_klines(self, symbol: str, interval: str, rows: Sequence[tuple]) -> int:
        """rows: (open_time, close_time, o,h,l,c, volume, quote_volume, trades)"""
        payload = [
            (
                symbol,
                interval,
                normalise_timestamp(r[0]),
                normalise_timestamp(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
                float(r[6]),
                float(r[7]),
                int(r[8]),
            )
            for r in rows
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)", payload
            )
        return len(payload)

    def insert_funding(self, symbol: str, rows: Sequence[tuple]) -> int:
        payload = [
            (symbol, normalise_timestamp(r[0]), float(r[1])) for r in rows
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO funding VALUES (?,?,?)", payload
            )
        return len(payload)

    def insert_filters(self, snapshot_time: int, rows: Sequence[dict]) -> int:
        payload = [
            (
                r["symbol"],
                normalise_timestamp(snapshot_time),
                r.get("status", "UNKNOWN"),
                r.get("min_notional"),
                r.get("step_size"),
                r.get("tick_size"),
            )
            for r in rows
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO symbol_filters VALUES (?,?,?,?,?,?)", payload
            )
        return len(payload)

    # ---- reads ----

    def view_as_of(self, as_of: int, enforce_monotonic: bool = True) -> PITView:
        """
        Open a view frozen at `as_of`.

        With enforce_monotonic, a run may not step backwards in time. Stepping
        backwards is almost always a bug (a reused loop variable, a retry that
        rewinds), and silently allowing it produces results that look fine.
        """
        as_of = normalise_timestamp(as_of)
        if enforce_monotonic and self._last_as_of is not None:
            if as_of < self._last_as_of:
                raise LookaheadError(
                    f"clock moved backwards: {as_of} < {self._last_as_of}. "
                    "Pass enforce_monotonic=False only for deliberate re-reads."
                )
        self._last_as_of = as_of
        return PITView(self._conn, as_of, self._cache)

    def reset_clock(self) -> None:
        self._last_as_of = None

    def iter_views(self, start: int, end: int, step_ms: int) -> Iterator[PITView]:
        """Walk the simulated clock forward. The backtest's main loop."""
        t = normalise_timestamp(start)
        end = normalise_timestamp(end)
        while t <= end:
            yield self.view_as_of(t)
            t += step_ms

    # ---- audit ----

    def audit_filter_coverage(self) -> dict:
        """
        Report the known-unknowns rather than hiding them.

        Binance publishes only current exchange filters, so any backtest
        before the first snapshot applies present-day MIN_NOTIONAL to past
        dates. Record snapshots daily from now on to shrink this gap.
        """
        row = self._conn.execute(
            "SELECT MIN(snapshot_time), MAX(snapshot_time), COUNT(DISTINCT symbol) "
            "FROM symbol_filters"
        ).fetchone()
        earliest_bar = self._conn.execute(
            "SELECT MIN(close_time) FROM klines"
        ).fetchone()[0]
        return {
            "earliest_filter_snapshot": row[0],
            "latest_filter_snapshot": row[1],
            "symbols_with_filters": row[2] or 0,
            "earliest_bar": earliest_bar,
            "unverified_filter_span_ms": (
                (row[0] - earliest_bar) if (row[0] and earliest_bar) else None
            ),
        }

    def coverage(self, interval: str = "1d") -> list[tuple]:
        return self._conn.execute(
            """
            SELECT symbol, COUNT(*), MIN(close_time), MAX(close_time)
              FROM klines WHERE interval = ?
             GROUP BY symbol ORDER BY symbol
            """,
            (interval,),
        ).fetchall()

    def close(self) -> None:
        self._conn.close()
