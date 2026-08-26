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


def settlement_times(after_ms: int, until_ms: int) -> list[int]:
    """
    The 8h settlement boundaries in (after_ms, until_ms]. Epoch ms 0 is
    1970-01-01T00:00 UTC, so multiples of 8h are exactly the 00/08/16 UTC
    settlement instants.

    Used to *count* funding data gaps rather than silently treating a missing
    rate as zero. The run continues, but the gap count is reported.
    """
    first = (after_ms // SETTLEMENT_INTERVAL_MS + 1) * SETTLEMENT_INTERVAL_MS
    return list(range(first, until_ms + 1, SETTLEMENT_INTERVAL_MS))


def expected_settlement_count(after_ms: int, until_ms: int) -> int:
    """How many settlement boundaries fall in (after_ms, until_ms]."""
    return len(settlement_times(after_ms, until_ms))
