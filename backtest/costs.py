"""
Fees and funding accrual.

Cost accounting is where backtests go quietly wrong, so this module is small,
pure, and tested in isolation (tests 3, 4, 9) before any strategy exists.

FUNDING SIGN CONVENTION
-----------------------
Positive funding rate means LONGS PAY SHORTS. A long position (units > 0)
loses `units * mark * rate`; a short receives it. Getting this backwards
inverts the results, so `funding_cashflow` is the single place the sign
lives and test 9 asserts it.

FUNDING MARK PRICE
------------------
Settlements land at 00:00 / 08:00 / 16:00 UTC. With daily bars the only
observed price on day D before its close is the open (== the 00:00 print),
so the day's open marks all three settlements. Exact for 00:00; an
approximation for 08:00/16:00 that uses no future information — the close
of day D is not knowable at 16:00. Documented in NOTES.md.
"""

from __future__ import annotations

FEE_RATES = {
    "taker": 0.0005,  # 0.05%
    "maker": 0.0002,  # 0.02%
}

# Binance USDS-M funding settles every 8 hours at 00:00/08:00/16:00 UTC.
SETTLEMENT_INTERVAL_MS = 8 * 3_600_000


def fee_rate(fee_mode: str) -> float:
    """Fee rate for a mode; raises on unknown modes rather than defaulting."""
    try:
        return FEE_RATES[fee_mode]
    except KeyError:
        raise ValueError(f"unknown fee_mode: {fee_mode!r}") from None


def slippage_bps(symbol: str, view, cfg) -> float:
    """
    Slippage assumption for one fill, in bps per side (Stage 2c 4.1 hook).

    Returns the flat config value. A per-symbol model (BTCUSDT under a
    basis point, mid-cap alts 5-20bps) can drop in here once a baseline
    exists and live/costlog.py has accumulated tiered data; fitting one
    before the baseline would be an extra trial with nothing to measure
    against.
    """
    return float(cfg.slippage_bps_per_side)


def slip_price(open_px: float, delta_units: float, bps: float) -> float:
    """Fill price adverse to the trade direction: buys pay up, sells hit down."""
    if delta_units == 0.0 or bps == 0.0:
        return open_px
    sign = 1.0 if delta_units > 0 else -1.0
    return open_px * (1.0 + sign * bps / 1e4)


def trade_fee(delta_units: float, fill_price: float, fee_mode: str) -> float:
    """
    Fee for one fill, as a positive cost.

    Fees apply to turnover — the traded delta — not to the position held.
    An unchanged position trades nothing and pays nothing.
    """
    if fill_price <= 0:
        raise ValueError(f"non-positive fill price: {fill_price}")
    return abs(delta_units) * fill_price * fee_rate(fee_mode)


def funding_cashflow(units: float, mark_price: float, rate: float) -> float:
    """
    Signed equity change from one funding settlement.

    Positive rate, long position (units > 0)  -> negative (long pays).
    Positive rate, short position (units < 0) -> positive (short receives).
    """
    if mark_price <= 0:
        raise ValueError(f"non-positive mark price: {mark_price}")
    return -units * mark_price * rate


def settlements_between(
    view, symbol: str, after_ms: int, until_ms: int
) -> list[tuple[int, float]]:
    """
    Funding settlements with after_ms < funding_time <= until_ms.

    `view.funding(since=...)` is inclusive on both ends and gated at as_of,
    so the half-open window is made by shifting `since` one ms past the
    previous as_of. `until_ms` must not exceed view.as_of — the view enforces
    the future side; the assert catches a caller walking the wrong clock.
    """
    assert until_ms <= view.as_of, "settlement window reaches past the view"
    return [
        (t, r)
        for t, r in view.funding(symbol, since=after_ms + 1)
        if t <= until_ms
    ]


def settlement_times(
    after_ms: int, until_ms: int, interval_ms: int = SETTLEMENT_INTERVAL_MS
) -> list[int]:
    """
    The 8h settlement boundaries in (after_ms, until_ms]. Epoch ms 0 is
    1970-01-01T00:00 UTC, so multiples of 8h are exactly the 00/08/16 UTC
    settlement instants.

    Used to *count* funding data gaps rather than silently treating a missing
    rate as zero. The run continues, but the gap count is reported.
    """
    first = (after_ms // interval_ms + 1) * interval_ms
    return list(range(first, until_ms + 1, interval_ms))


def expected_settlement_count(
    after_ms: int, until_ms: int, interval_ms: int = SETTLEMENT_INTERVAL_MS
) -> int:
    """How many settlement boundaries fall in (after_ms, until_ms] for a
    symbol settling every `interval_ms`."""
    first = (after_ms // interval_ms + 1) * interval_ms
    return len(range(first, until_ms + 1, interval_ms))


# Binance moved some symbols to 4-hourly funding. The interval is not stored
# in the dataset, so it is INFERRED from each symbol's own settlement
# timestamps up to as_of -- point-in-time safe, and more robust than a
# current-snapshot field would be, since it reflects the cadence actually in
# force during the window being measured.
SUPPORTED_INTERVALS_MS = (4 * 3_600_000, SETTLEMENT_INTERVAL_MS)


def infer_funding_interval_ms(view, symbol: str, lookback_ms: int = 7 * 86_400_000) -> int:
    """
    Modal gap between the symbol's recent settlements, snapped to a supported
    interval. Falls back to the 8h default when there is too little history
    to tell -- never to a guess that would understate the expected count.
    """
    rows = view.funding(symbol, since=view.as_of - lookback_ms)
    if len(rows) < 3:
        return SETTLEMENT_INTERVAL_MS
    diffs = [b[0] - a[0] for a, b in zip(rows, rows[1:]) if b[0] > a[0]]
    if not diffs:
        return SETTLEMENT_INTERVAL_MS
    diffs.sort()
    median = diffs[len(diffs) // 2]
    return min(SUPPORTED_INTERVALS_MS, key=lambda i: abs(i - median))
