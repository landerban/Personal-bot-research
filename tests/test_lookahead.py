"""
Adversarial tests against the point-in-time guarantee.

These do not check that the happy path works. They try to *break* the
invariant, because a lookahead bug produces a beautiful backtest and no error
message, and is therefore the failure mode least likely to be noticed.
"""

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pitdata.store import (  # noqa: E402
    LookaheadError,
    PointInTimeStore,
    normalise_timestamp,
)

DAY = 86_400_000
T0 = 1_600_000_000_000  # arbitrary ms epoch, aligned below


def build_store(n_days=120, symbols=("AAAUSDT", "BBBUSDT")):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    for si, sym in enumerate(symbols):
        rows = []
        for d in range(n_days):
            open_time = T0 + d * DAY
            close_time = open_time + DAY - 1
            # deterministic, distinguishable per symbol and day
            price = 100.0 + d + si * 1000
            rows.append(
                (open_time, close_time, price, price + 1, price - 1, price,
                 10.0, 1_000_000.0 * (si + 1), 100)
            )
        store.insert_klines(sym, "1d", rows)
        # funding at 00:00 / 08:00 / 16:00
        frows = []
        for d in range(n_days):
            for h in (0, 8, 16):
                frows.append((T0 + d * DAY + h * 3_600_000, 0.0001))
        store.insert_funding(sym, frows)
    return store


def test_bar_invisible_until_closed():
    """A bar must not appear until its close_time has passed."""
    s = build_store()
    day5_open = T0 + 5 * DAY
    day5_close = day5_open + DAY - 1

    v = s.view_as_of(day5_close - 1)  # one ms before close
    bars = v.klines("AAAUSDT", limit=1000)
    assert all(b.close_time <= day5_close - 1 for b in bars)
    assert day5_close not in [b.close_time for b in bars], "unclosed bar leaked"

    s.reset_clock()
    v2 = s.view_as_of(day5_close)  # exactly at close
    assert day5_close in [b.close_time for b in v2.klines("AAAUSDT", limit=1000)]
    print("PASS bar_invisible_until_closed")


def test_partial_bar_never_visible_midday():
    """Mid-session, today's forming bar must be invisible."""
    s = build_store()
    midday = T0 + 10 * DAY + DAY // 2
    v = s.view_as_of(midday)
    bars = v.klines("AAAUSDT", limit=1000)
    assert bars[-1].close_time < midday
    assert bars[-1].open_time == T0 + 9 * DAY, "today's partial bar leaked"
    print("PASS partial_bar_never_visible_midday")


def test_latest_close_is_gated():
    s = build_store()
    v = s.view_as_of(T0 + 20 * DAY + DAY // 2)
    # day 19 is the last completed bar -> price 100+19
    assert v.latest_close("AAAUSDT") == 119.0
    print("PASS latest_close_is_gated")


def test_trailing_return_skip_excludes_recent_bars():
    """skip=2 must exclude the two most recent completed bars."""
    s = build_store()
    as_of = T0 + 50 * DAY - 1  # day 49 just closed; prices are 100+d
    v = s.view_as_of(as_of)

    r0 = v.trailing_return("AAAUSDT", lookback=10, skip=0)
    r2 = v.trailing_return("AAAUSDT", lookback=10, skip=2)

    # skip=0: bars 39..49 -> 149/139 - 1
    assert math.isclose(r0, 149 / 139 - 1, rel_tol=1e-12), r0
    # skip=2: bars 37..47 -> 147/137 - 1
    assert math.isclose(r2, 147 / 137 - 1, rel_tol=1e-12), r2
    print("PASS trailing_return_skip_excludes_recent_bars")


def test_insufficient_history_returns_none():
    """No silent partial windows -- short history must return None."""
    s = build_store()
    v = s.view_as_of(T0 + 3 * DAY)
    assert v.trailing_return("AAAUSDT", lookback=30) is None
    assert v.realised_vol("AAAUSDT", window=30) is None
    print("PASS insufficient_history_returns_none")


def test_funding_gated_at_settlement():
    s = build_store()
    settle = T0 + 5 * DAY + 8 * 3_600_000
    s.reset_clock()
    v_before = s.view_as_of(settle - 1)
    assert settle not in [t for t, _ in v_before.funding("AAAUSDT")]
    s.reset_clock()
    v_at = s.view_as_of(settle)
    assert settle in [t for t, _ in v_at.funding("AAAUSDT")]
    print("PASS funding_gated_at_settlement")


def test_universe_is_point_in_time():
    """A symbol must not enter the universe before it has enough history."""
    s = build_store(n_days=120, symbols=("AAAUSDT",))
    # BBB lists 60 days late
    rows = []
    for d in range(60, 120):
        ot = T0 + d * DAY
        rows.append((ot, ot + DAY - 1, 50.0 + d, 51.0 + d, 49.0 + d, 50.0 + d,
                     10.0, 5_000_000.0, 100))
    s.insert_klines("BBBUSDT", "1d", rows)

    u_early = s.view_as_of(T0 + 80 * DAY).universe(
        min_quote_volume=1.0, lookback_days=30, min_history_days=60
    )
    assert "BBBUSDT" not in u_early, "symbol entered universe before 60d history"

    s.reset_clock()
    # NB: the *close* of day 119, not its open. At the open, only bars 60..118
    # have completed -- 59, not 60. Getting this backwards is how lookahead
    # enters a backtest, so the test states it explicitly.
    u_late = s.view_as_of(T0 + 120 * DAY - 1).universe(
        min_quote_volume=1.0, lookback_days=30, min_history_days=60
    )
    assert "BBBUSDT" in u_late, "symbol missing after 60 completed bars"
    print("PASS universe_is_point_in_time")


def test_liquidity_threshold_uses_median_not_mean():
    """A single volume spike must not promote an illiquid symbol."""
    s = build_store(n_days=120, symbols=("AAAUSDT",))
    rows = []
    for d in range(120):
        ot = T0 + d * DAY
        qv = 1e9 if d == 119 else 1.0  # one enormous day
        rows.append((ot, ot + DAY - 1, 10.0, 11.0, 9.0, 10.0, 1.0, qv, 10))
    s.insert_klines("SPIKEUSDT", "1d", rows)

    u = s.view_as_of(T0 + 119 * DAY).universe(
        min_quote_volume=1000.0, lookback_days=30, min_history_days=60
    )
    assert "SPIKEUSDT" not in u, "volume spike defeated the liquidity filter"
    print("PASS liquidity_threshold_uses_median_not_mean")


def test_clock_cannot_move_backwards():
    s = build_store()
    s.view_as_of(T0 + 50 * DAY)
    try:
        s.view_as_of(T0 + 10 * DAY)
    except LookaheadError:
        print("PASS clock_cannot_move_backwards")
        return
    raise AssertionError("clock moved backwards without error")


def test_no_raw_sql_escape_hatch():
    """PITView must expose no ungated path to the database."""
    s = build_store()
    v = s.view_as_of(T0 + 10 * DAY)
    public = [a for a in dir(v) if not a.startswith("_")]
    forbidden = {"execute", "executemany", "cursor", "connection", "conn", "raw"}
    leaked = forbidden.intersection(public)
    assert not leaked, f"escape hatch exposed: {leaked}"
    # __slots__ must prevent attaching a connection later
    try:
        v.conn = "x"
    except AttributeError:
        pass
    else:
        raise AssertionError("PITView is not slot-locked")
    print("PASS no_raw_sql_escape_hatch")


def test_every_public_reader_is_time_gated():
    """
    Sweep every public read method at an early as_of and assert nothing
    returned carries a timestamp past it. Catches a future method added
    without a gate.
    """
    s = build_store()
    as_of = T0 + 30 * DAY + 500
    v = s.view_as_of(as_of)

    for b in v.klines("AAAUSDT", limit=10_000):
        assert b.close_time <= as_of, f"klines leaked {b.close_time}"
    for t, _ in v.funding("AAAUSDT"):
        assert t <= as_of, f"funding leaked {t}"

    readers = [m for m in dir(v) if not m.startswith("_") and callable(getattr(v, m))]
    expected = {
        "klines", "latest_close", "trailing_return", "realised_vol",
        "funding", "universe", "min_notional", "tradeable_universe",
        # Stage 17 I.3, enrolled DELIBERATELY (NOTES 57.7). Returns the
        # EARLIEST recorded lot/tick/notional filters for a symbol and is
        # therefore NOT point-in-time -- exactly and only the gap
        # `min_notional` already carries, because Binance publishes no filter
        # history. Same table, same earliest row, and audit_filter_coverage()
        # already quantifies the unverified span. Adding step_size/tick_size
        # extends that ACCEPTED gap to two more columns; it does not create a
        # new class of leak.
        #
        # This whitelist exists to make such an addition a reviewed act rather
        # than an accident, and it did its job: the method was caught on its
        # first run.
        "symbol_filters",
    }
    unknown = set(readers) - expected
    assert not unknown, f"ungated reader may have been added: {unknown}"
    print("PASS every_public_reader_is_time_gated")


def test_microsecond_timestamps_normalised():
    assert normalise_timestamp(1_700_000_000_000) == 1_700_000_000_000
    assert normalise_timestamp(1_700_000_000_000_000) == 1_700_000_000_000
    print("PASS microsecond_timestamps_normalised")


def test_tradeable_universe_respects_min_notional():
    """At $100 capital, symbols with a high MIN_NOTIONAL must be excluded."""
    s = build_store(n_days=120, symbols=("CHEAPUSDT", "PRICEYUSDT"))
    s.insert_filters(
        T0,
        [
            {"symbol": "CHEAPUSDT", "status": "TRADING", "min_notional": 5.0},
            {"symbol": "PRICEYUSDT", "status": "TRADING", "min_notional": 100.0},
        ],
    )
    v = s.view_as_of(T0 + 119 * DAY)
    # $100 capital, 2x gross, 10 positions -> avg $20, smallest 0.5*20 = $10:
    # clears the $5 floor, not the $100 one. (Was 0.25*20 = $5 before Stage
    # 2b corrected the double-counted vol factor; intent unchanged.)
    u = v.tradeable_universe(
        capital=100.0, gross_leverage=2.0, n_positions=10,
        min_quote_volume=1.0, lookback_days=30, min_history_days=60,
    )
    assert "CHEAPUSDT" in u
    assert "PRICEYUSDT" not in u, "symbol above MIN_NOTIONAL was not excluded"
    print("PASS tradeable_universe_respects_min_notional")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
