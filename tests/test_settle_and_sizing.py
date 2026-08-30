"""
Stage 17 Part I tests: the settle primitive, the measured floor rule, the
shared sizing module, and the repaired `leg_beta_se`.

Each of the four defects the review named gets a test that would have caught
it, so the fix cannot silently regress.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.sizing import (  # noqa: E402
    FLOOR_EXEMPT_CLASSES, FLOOR_RULE, FULL_CLOSE, OPEN_INCREASE, PARTIAL_REDUCE,
    FLIP, SymbolFilters, classify_delta, quantize_qty, size_from_weight,
    size_order,
)
from backtest.weights import leg_beta_se  # noqa: E402
from live.settle import (  # noqa: E402
    SettleTimeout, await_reconciled_state,
)


# ------------------------------------------------- I.1 settle primitive

class LaggyClient:
    """positionRisk reports flat for the first N polls, then the truth --
    the behaviour measured in §56.8-3."""

    def __init__(self, truth: dict, lag_polls: int):
        self.truth = truth
        self.lag = lag_polls
        self.polls = 0

    def positions(self):
        self.polls += 1
        return {} if self.polls <= self.lag else dict(self.truth)

    def open_orders(self, symbol=None):
        return []

    def equity(self):
        return 5000.0


def test_settle_waits_out_a_laggy_exchange():
    c = LaggyClient({"AAAUSDT": 1.0}, lag_polls=3)
    out = await_reconciled_state(c, {"AAAUSDT": 1.0},
                                 step_sizes={"AAAUSDT": 0.001},
                                 sleeper=lambda s: None)
    assert out.settled and out.polls == 4
    assert out.observed == {"AAAUSDT": 1.0}
    print(f"PASS settle_waits_out_lag ({out.polls} polls)")


def test_settle_raises_rather_than_returning_an_unsettled_book():
    """A genuinely missing leg must NOT be reported as settled -- otherwise
    the atomicity check renders a verdict on a book nobody confirmed."""
    c = LaggyClient({"AAAUSDT": 1.0}, lag_polls=10_000)
    ticks = iter(range(0, 100))
    with pytest.raises(SettleTimeout, match="did not settle"):
        await_reconciled_state(c, {"AAAUSDT": 1.0},
                               step_sizes={"AAAUSDT": 0.001},
                               timeout_s=3, sleeper=lambda s: None,
                               clock=lambda: next(ticks))
    print("PASS settle_raises_on_missing_leg")


def test_settle_tolerance_is_the_step_size():
    c = LaggyClient({"AAAUSDT": 1.0005}, lag_polls=0)
    out = await_reconciled_state(c, {"AAAUSDT": 1.0},
                                 step_sizes={"AAAUSDT": 0.001},
                                 sleeper=lambda s: None)
    assert out.settled, "within 1.5x step should count as settled"

    c2 = LaggyClient({"AAAUSDT": 1.5}, lag_polls=0)
    ticks = iter(range(0, 100))
    with pytest.raises(SettleTimeout):
        await_reconciled_state(c2, {"AAAUSDT": 1.0},
                               step_sizes={"AAAUSDT": 0.001}, timeout_s=2,
                               sleeper=lambda s: None,
                               clock=lambda: next(ticks))
    print("PASS settle_step_tolerance")


def test_there_is_exactly_one_settle_implementation():
    """Both call sites must import the SAME function -- the defect was two
    behaviours, one of them a bare sleep and the other absent."""
    rt = (ROOT / "tools" / "roundtrip_demo.py").read_text(encoding="utf-8")
    ph = (ROOT / "live" / "phase2.py").read_text(encoding="utf-8")
    for src, name in ((rt, "roundtrip_demo"), (ph, "phase2")):
        assert "await_reconciled_state" in src, f"{name} does not settle"
    assert "time.sleep" not in rt, "roundtrip still sleeps instead of settling"
    # and only one definition exists anywhere
    defs = [p for p in (ROOT / "live").glob("*.py")
            if "def await_reconciled_state" in p.read_text(encoding="utf-8")]
    assert len(defs) == 1, f"multiple settle implementations: {defs}"
    print("PASS settle_single_implementation")


# --------------------------------------------- I.2 the measured floor rule

def test_the_floor_rule_is_the_one_the_venue_gave():
    """Probed 2026-08-30: reduce-only sub-floor ACCEPTED, the identical size
    without reduce-only REJECTED -4164 (tools/floor_probe.py)."""
    assert FLOOR_RULE == "reduce_only_exempt"
    assert PARTIAL_REDUCE in FLOOR_EXEMPT_CLASSES
    assert FULL_CLOSE in FLOOR_EXEMPT_CLASSES
    assert OPEN_INCREASE not in FLOOR_EXEMPT_CLASSES
    assert FLIP not in FLOOR_EXEMPT_CLASSES, (
        "a flip crosses zero and cannot be one reduce-only order")
    print("PASS floor_rule_matches_the_probe")


def test_delta_classification_covers_every_case():
    assert classify_delta(0.0, 1.0) == OPEN_INCREASE
    assert classify_delta(1.0, 2.0) == OPEN_INCREASE
    assert classify_delta(2.0, 1.0) == PARTIAL_REDUCE
    assert classify_delta(-2.0, -1.0) == PARTIAL_REDUCE
    assert classify_delta(1.0, 0.0) == FULL_CLOSE
    assert classify_delta(-1.0, 0.0) == FULL_CLOSE
    assert classify_delta(1.0, -1.0) == FLIP
    assert classify_delta(-1.0, 1.0) == FLIP
    print("PASS delta_classification")


def test_the_unclosable_position_trap():
    """THE trap the review named: a position whose CLOSING delta is under the
    floor. Under the old strict rule it could never be closed."""
    f = SymbolFilters("AAAUSDT", min_notional=5.0, step_size=0.01)

    closing = size_order("AAAUSDT", current_units=0.03, target_units=0.0,
                         price=100.0, filters=f)
    assert closing.delta_class == FULL_CLOSE
    assert closing.notional == pytest.approx(3.0)
    assert closing.ok, "a sub-floor CLOSE must be allowed -- the venue allows it"

    reducing = size_order("AAAUSDT", current_units=0.10, target_units=0.07,
                          price=100.0, filters=f)
    assert reducing.delta_class == PARTIAL_REDUCE and reducing.ok

    # ...but an OPEN of the same size is still refused, as the control showed
    opening = size_order("AAAUSDT", current_units=0.0, target_units=0.03,
                         price=100.0, filters=f)
    assert opening.delta_class == OPEN_INCREASE
    assert not opening.ok and "below_min_notional" in opening.reason
    print("PASS unclosable_position_trap")


# ------------------------------------------------ I.3 the sizing module

def test_quantization_floors_and_never_rounds_up():
    assert quantize_qty(0.0079, 0.001) == pytest.approx(0.007)
    assert quantize_qty(-0.0079, 0.001) == pytest.approx(0.007)
    assert quantize_qty(12.9, 1.0) == pytest.approx(12.0)
    assert quantize_qty(0.5, None) == pytest.approx(0.5)      # unknown step
    assert quantize_qty(0.5, 0) == pytest.approx(0.5)
    print("PASS sizing_quantize_floors")


def test_a_position_can_be_quantized_out_of_existence():
    """The review's real point: BTC's 0.001 step is ~$78 at $78k, so a $19
    target is not a small position -- it is NO position. Research sizing,
    which never quantized, believed it existed."""
    f = SymbolFilters("BTCUSDT", min_notional=50.0, step_size=0.001)
    s = size_from_weight("BTCUSDT", weight=0.024, equity=800.0,
                         price=78_000.0, filters=f)
    assert s.raw_qty > 0
    assert s.qty == 0.0
    assert s.quantized_away is True
    assert not s.ok and "quantized_to_zero" in s.reason
    print(f"PASS sizing_quantized_away (raw {s.raw_qty:.8f} -> 0)")


def test_the_reviews_worked_example():
    """$5.04 intended -> quantized down -> under the floor -> rejected."""
    f = SymbolFilters("AAAUSDT", min_notional=5.0, step_size=0.01)
    s = size_order("AAAUSDT", 0.0, 0.0504, price=100.0, filters=f)
    assert s.qty == pytest.approx(0.05)
    assert s.notional == pytest.approx(5.00)
    assert s.ok, "exactly at the floor is allowed"

    s2 = size_order("AAAUSDT", 0.0, 0.0499, price=100.0, filters=f)
    assert s2.qty == pytest.approx(0.04)
    assert s2.notional == pytest.approx(4.00)
    assert not s2.ok and "below_min_notional" in s2.reason
    print(f"PASS sizing_worked_example (0.0499 -> {s2.qty} -> "
          f"${s2.notional:.2f} rejected)")


def test_unknown_filters_are_unknown_not_unconstrained():
    """A missing floor must not be read as 'no floor'."""
    f = SymbolFilters("AAAUSDT", min_notional=None, step_size=None)
    s = size_order("AAAUSDT", 0.0, 0.001, price=1.0, filters=f)
    assert s.ok and s.qty == pytest.approx(0.001)
    assert s.notional == pytest.approx(0.001)
    print("PASS sizing_unknown_filters")


# ------------------------------------------------- I.4 leg_beta_se

def test_leg_beta_se_returns_what_its_docstring_promises():
    """It used to return (se, se) -- never computing the contribution -- and
    had zero callers."""
    weights = {"A": 0.3, "B": 0.2, "C": -0.25, "D": -0.25}
    betas = {"A": 1.0, "B": 2.0, "C": 1.0, "D": 3.0}
    ses = {"A": 0.1, "B": 0.2, "C": 0.1, "D": 0.3}

    contrib, se = leg_beta_se(weights, betas, ses, positive=True)
    assert contrib == pytest.approx(0.3 * 1.0 + 0.2 * 2.0)
    assert se == pytest.approx(((0.3 * 0.1) ** 2 + (0.2 * 0.2) ** 2) ** 0.5)
    assert contrib != se, "the old bug returned the SE for both"

    c2, se2 = leg_beta_se(weights, betas, ses, positive=False)
    assert c2 == pytest.approx(0.25 * 1.0 + 0.25 * 3.0)
    assert leg_beta_se({}, {}, {}, True) == (0.0, 0.0)
    print("PASS leg_beta_se_matches_docstring")


def test_leg_beta_se_is_actually_used():
    """No authoritative-looking dead code: build() must call it rather than
    open-coding a second copy."""
    src = (ROOT / "backtest" / "weights.py").read_text(encoding="utf-8")
    assert "contrib, se_leg = leg_beta_se(" in src
    assert src.count("def leg_beta_se") == 1
    print("PASS leg_beta_se_wired_in")
