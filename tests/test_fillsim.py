"""
Stage 16 C.3: fill-simulator tests.

The simulator decides what the paper book "paid", so its arithmetic and
especially its SIGN are load-bearing. A slippage model that is symmetric in
expectation would flatter a momentum strategy, because the execution delay is
one-signed against it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live.costlog import CostLog  # noqa: E402
from live.fillsim import (  # noqa: E402
    EXECUTION_DELAY_MS, SLIPPAGE_BPS, USDC_TAKER, USDT_TAKER, VENUE_TAG,
    adverse_fill_price, execution_bar, maker_counterfactual, record, simulate,
)

MIN = 60_000


def bar(open_time, o, h, l, c):
    """A Binance kline row: [openTime, o, h, l, c, vol, closeTime, ...]."""
    return [open_time, str(o), str(h), str(l), str(c), "1",
            open_time + MIN - 1, "1000", 10, "0", "0", "0"]


class FakeClient:
    def __init__(self, bars):
        self.bars = bars           # symbol -> list of rows
        self.calls = 0

    def klines(self, symbol, interval="1m", limit=5, start_ms=None, end_ms=None):
        self.calls += 1
        rows = self.bars.get(symbol, [])
        if start_ms is not None:
            rows = [r for r in rows if r[0] >= start_ms - MIN]
        return rows[:limit]


# ------------------------------------------------------- the fill model

def test_slippage_is_adverse_in_both_directions():
    """A buy fills ABOVE the open and a sell BELOW it. Not symmetric noise --
    against a momentum signal the delay is one-signed."""
    assert adverse_fill_price(100.0, "BUY") == pytest.approx(100.05)
    assert adverse_fill_price(100.0, "SELL") == pytest.approx(99.95)
    assert adverse_fill_price(100.0, "BUY") > 100.0
    assert adverse_fill_price(100.0, "SELL") < 100.0
    assert SLIPPAGE_BPS == 5.0, "NOTES 56.2 fixed this; do not tune"
    print("PASS fillsim_adverse_sign")


def test_the_execution_bar_opens_STRICTLY_after_the_decision():
    """A bar already open at decision time was partly formed then; filling at
    its open would be filling at a price the decision could have seen."""
    t = 1_800_000_000_000
    rows = [bar(t - MIN, 10, 11, 9, 10), bar(t, 20, 21, 19, 20),
            bar(t + MIN, 30, 31, 29, 30)]
    c = FakeClient({"AAAUSDT": rows})
    got = execution_bar(c, "AAAUSDT", decision_ms=t)
    assert int(got[0]) == t + MIN, "must skip the bar open AT the decision"
    got2 = execution_bar(c, "AAAUSDT", decision_ms=t - 1)
    assert int(got2[0]) == t
    assert EXECUTION_DELAY_MS == MIN
    print("PASS fillsim_execution_bar_strictly_after")


def test_no_bar_means_no_fill_rather_than_a_guess():
    c = FakeClient({"AAAUSDT": [bar(1000, 10, 11, 9, 10)]})
    out = simulate(c, {"AAAUSDT": 1.0}, {}, {"AAAUSDT": 10.0},
                   decision_ms=10_000_000)
    assert out.fills == []
    assert out.missing_bars == ["AAAUSDT"]
    print("PASS fillsim_missing_bar_is_not_a_fill")


def test_min_notional_is_refused_exactly_as_the_venue_would():
    """The simulator must not be kinder than the exchange, or the paper book
    becomes one the venue would never have allowed."""
    t = 1_800_000_000_000
    c = FakeClient({"AAAUSDT": [bar(t + MIN, 100.0, 101, 99, 100)]})
    out = simulate(c, {"AAAUSDT": 0.01}, {}, {"AAAUSDT": 100.0},
                   decision_ms=t, min_notionals={"AAAUSDT": 50.0})
    assert out.fills == []
    assert out.refused[0]["reason"] == "below_min_notional"
    assert out.refused[0]["notional"] == pytest.approx(1.0)

    out2 = simulate(c, {"AAAUSDT": 1.0}, {}, {"AAAUSDT": 100.0},
                    decision_ms=t, min_notionals={"AAAUSDT": 50.0})
    assert len(out2.fills) == 1 and not out2.refused
    print("PASS fillsim_min_notional_refusal")


def test_fees_are_reported_as_a_pair_never_selected_between():
    t = 1_800_000_000_000
    c = FakeClient({"AAAUSDT": [bar(t + MIN, 100.0, 101, 99, 100)]})
    out = simulate(c, {"AAAUSDT": 2.0}, {}, {"AAAUSDT": 100.0}, decision_ms=t)
    f = out.fills[0]
    assert f.fill_price == pytest.approx(100.05)
    assert f.notional == pytest.approx(200.10)
    assert f.fee_usdt == pytest.approx(f.notional * USDT_TAKER)
    assert f.fee_usdc == pytest.approx(f.notional * USDC_TAKER)
    assert f.fee_usdc < f.fee_usdt
    print("PASS fillsim_fee_pair")


def test_a_sell_delta_fills_below_and_nets_correctly():
    t = 1_800_000_000_000
    c = FakeClient({"AAAUSDT": [bar(t + MIN, 100.0, 101, 99, 100)]})
    out = simulate(c, {"AAAUSDT": -1.0}, {"AAAUSDT": 2.0},
                   {"AAAUSDT": 100.0}, decision_ms=t)
    f = out.fills[0]
    assert f.side == "SELL" and f.qty == pytest.approx(3.0)
    assert f.fill_price == pytest.approx(99.95)
    assert out.filled_units == {"AAAUSDT": pytest.approx(-3.0)}
    print("PASS fillsim_sell_side")


def test_zero_delta_produces_no_order():
    t = 1_800_000_000_000
    c = FakeClient({"AAAUSDT": [bar(t + MIN, 100.0, 101, 99, 100)]})
    out = simulate(c, {"AAAUSDT": 1.0}, {"AAAUSDT": 1.0},
                   {"AAAUSDT": 100.0}, decision_ms=t)
    assert out.fills == [] and c.calls == 0, "no bar should even be fetched"
    print("PASS fillsim_no_op_delta")


# ------------------------------------------- spread + shadow-maker (D.1)

def test_half_spread_is_captured_for_context_only():
    t = 1_800_000_000_000
    c = FakeClient({"AAAUSDT": [bar(t + MIN, 100.0, 101, 99, 100)]})
    out = simulate(c, {"AAAUSDT": 1.0}, {}, {"AAAUSDT": 100.0}, decision_ms=t,
                   quotes={"AAAUSDT": (99.98, 100.02)})
    f = out.fills[0]
    assert f.bid_at_decision == 99.98 and f.ask_at_decision == 100.02
    assert f.half_spread_bps == pytest.approx(2.0, abs=0.01)
    # the assumption is NOT updated from the measurement
    assert f.slippage_bps == SLIPPAGE_BPS
    print(f"PASS fillsim_spread_context (half-spread {f.half_spread_bps:.2f} "
          f"bps vs assumption {SLIPPAGE_BPS})")


def test_shadow_maker_asks_whether_the_tape_traded_through():
    """D.1: logging only. No maker order is placed anywhere."""
    b = bar(0, 100.0, 101.0, 99.0, 100.0)
    price, would = maker_counterfactual(b, "BUY", bid=99.5, ask=100.5)
    assert price == 99.5 and would is True, "the bar traded down to 99.0"

    b2 = bar(0, 100.0, 101.0, 99.9, 100.0)
    price2, would2 = maker_counterfactual(b2, "BUY", bid=99.5, ask=100.5)
    assert price2 == 99.5 and would2 is False, "the bar never reached 99.5"

    price3, would3 = maker_counterfactual(b, "SELL", bid=99.5, ask=100.5)
    assert price3 == 100.5 and would3 is True, "the bar traded up to 101.0"

    assert maker_counterfactual(b, "BUY", None, None) == (None, None)
    print("PASS fillsim_shadow_maker")


# -------------------------------------------------------- costlog rows

def test_simulated_rows_are_tagged_so_they_cannot_be_mistaken_for_real(tmp_path):
    t = 1_800_000_000_000
    c = FakeClient({"AAAUSDT": [bar(t + MIN, 100.0, 101, 99, 100)]})
    out = simulate(c, {"AAAUSDT": 1.0}, {}, {"AAAUSDT": 100.0}, decision_ms=t)
    cl = CostLog(tmp_path / "costs.jsonl")
    assert record(cl, out) == 1
    rows = cl.records()
    assert rows[0]["venue"] == VENUE_TAG == "prod_data_sim"
    assert rows[0]["venue"] not in ("testnet", "production")
    assert rows[0]["order_type"] == "SIM_MARKET"
    print("PASS fillsim_venue_tag")
