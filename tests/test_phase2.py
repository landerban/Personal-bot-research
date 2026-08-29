"""
Stage 12 Part B: Phase-2 paper-cycle tests.

The day-1 live cycle SKIPPED (the frozen config skips ~21.5% of days at this
size, NOTES 43.6), so neither the shadow comparison nor the execution path was
exercised against the venue. These tests exercise both against synthetic
decisions, so the checks are known to work rather than merely known to run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import weights as W  # noqa: E402
from live.costlog import DEFAULT_VENUE, CostLog  # noqa: E402
from live.phase2 import WEIGHT_TOL, Phase2Config, Phase2Runner, order_id  # noqa: E402


class FakeDecision:
    """Just enough of weights.Decision for the shadow comparison."""

    def __init__(self, weights):
        self.final_weights = dict(weights)


class StubRunner(Phase2Runner):
    """Phase2Runner with the re-fetch replaced, so shadow_check can be driven
    to MATCH and to MISMATCH deterministically."""

    def __init__(self, shadow_weights, cfg=None):
        self.cfg = cfg or Phase2Config()
        self._clock = lambda: 1_800_000_000.0
        self._shadow_weights = shadow_weights
        self.c = None
        self.feed = None

    def shadow_check(self, symbols, equity, decision, filled_weights=None):
        import live.phase2 as p2

        class _Feed:
            def __init__(self, *a, **k):
                pass

            def build(self, syms, as_of=None):
                return None, None

            def close(self):
                pass

        real_feed, real_ctw = p2.LiveFeed, W.compute_target_weights
        sw = self._shadow_weights
        p2.LiveFeed = _Feed
        try:
            W.compute_target_weights = (
                lambda *a, **k: sw if isinstance(sw, W.Skip)
                else FakeDecision(sw))
            return Phase2Runner.shadow_check(
                self, symbols, equity, decision, filled_weights)
        finally:
            p2.LiveFeed = real_feed
            W.compute_target_weights = real_ctw


BOOK = {"AAAUSDT": 0.05, "BBBUSDT": 0.04, "CCCUSDT": -0.045, "DDDUSDT": -0.045}


def test_shadow_reports_match_when_the_refetch_agrees():
    r = StubRunner(dict(BOOK))
    out = r.shadow_check(list(BOOK), 800.0, FakeDecision(BOOK))
    assert out["result"] == "MATCH", out
    assert out["max_weight_delta"] <= WEIGHT_TOL
    print("PASS phase2_shadow_match")


def test_shadow_catches_a_weight_drift_above_tolerance():
    """The whole point of STAGE10 §3: if the live path and a re-decide differ,
    they are not the same strategy and the day is a stop-and-diagnose."""
    drifted = dict(BOOK, BBBUSDT=BOOK["BBBUSDT"] + 1e-4)
    r = StubRunner(drifted)
    out = r.shadow_check(list(BOOK), 800.0, FakeDecision(BOOK))
    assert out["result"] == "MISMATCH", out
    assert out["max_weight_delta"] > WEIGHT_TOL
    assert "BBBUSDT" in out["detail"]
    print("PASS phase2_shadow_mismatch")


def test_shadow_tolerance_boundary_is_not_a_mismatch():
    ok = dict(BOOK, BBBUSDT=BOOK["BBBUSDT"] + WEIGHT_TOL / 2)
    out = StubRunner(ok).shadow_check(list(BOOK), 800.0, FakeDecision(BOOK))
    assert out["result"] == "MATCH", out
    print("PASS phase2_shadow_boundary")


def test_shadow_catches_a_different_name_set():
    """A book with the same weights on different names is a different book."""
    other = {"AAAUSDT": 0.05, "ZZZUSDT": 0.04,
             "CCCUSDT": -0.045, "DDDUSDT": -0.045}
    out = StubRunner(other).shadow_check(list(BOOK), 800.0, FakeDecision(BOOK))
    assert out["result"] == "MISMATCH", out
    assert "different names" in out["detail"]
    assert "BBBUSDT" in out["detail"] and "ZZZUSDT" in out["detail"]
    print("PASS phase2_shadow_name_set")


def test_shadow_flags_a_refetch_that_skips_where_the_live_path_traded():
    out = StubRunner(W.Skip("insufficient_candidates", "x")).shadow_check(
        list(BOOK), 800.0, FakeDecision(BOOK))
    assert out["result"] == "MISMATCH", out
    assert "re-decide skipped" in out["detail"]
    print("PASS phase2_shadow_refetch_skip")


def test_shadow_reports_skip_without_pretending_to_have_checked():
    """A skipped decision must report SKIP, never MATCH. A vacuous pass is
    exactly what STAGE10 §3 must not produce (the Stage 2e trap)."""
    out = StubRunner(dict(BOOK)).shadow_check(
        list(BOOK), 800.0, W.Skip("below_min_notional_post_hedge", "2L/4S"))
    assert out["result"] == "SKIP", out
    assert out["result"] != "MATCH"
    assert "below_min_notional_post_hedge" in out["detail"]
    print("PASS phase2_shadow_skip_is_not_match")


def test_shadow_measures_fill_divergence_when_given_filled_weights():
    filled = dict(BOOK, AAAUSDT=BOOK["AAAUSDT"] - 0.01)
    out = StubRunner(dict(BOOK)).shadow_check(
        list(BOOK), 800.0, FakeDecision(BOOK), filled_weights=filled)
    assert out["result"] == "MATCH"
    assert out["max_fill_delta"] == pytest.approx(0.01, abs=1e-9)
    print("PASS phase2_shadow_fill_delta")


def test_frozen_config_maps_to_the_frozen_backtest_config():
    """NOTES 45.13 / 48.1: paper must run the config the research froze."""
    c = Phase2Config().to_backtest_config()
    assert (c.lookback, c.skip, c.n_positions) == (14, 0, 10)
    assert c.vol_target == 0.10
    assert c.initial_capital == 800.0
    assert c.max_liquidity_rank == 15
    assert c.rank_buffer == 0
    assert c.max_gross_leverage == 3.0
    assert c.fee_mode == "taker"
    print("PASS phase2_frozen_config")


def test_costlog_tags_every_row_with_the_venue(tmp_path):
    """STAGE10 §6: an untagged row cannot be filtered out of a real-cost
    estimate later, so the tag must exist from the first fill."""
    cl = CostLog(tmp_path / "costs.jsonl")
    cl.record_fill(symbol="AAAUSDT", side="BUY", intended_price=10.0,
                   fill_price=10.01, qty=1.0, fee=0.004, fee_asset="USDT",
                   maker=False, order_type="MARKET", ts_ms=1)
    cl.record_funding(symbol="AAAUSDT", position_units=1.0, mark=10.0,
                      rate=0.0001, actual_amount=-0.001, ts_ms=2)
    rows = cl.records()
    assert len(rows) == 2
    assert all(r["venue"] == DEFAULT_VENUE == "testnet" for r in rows), rows
    print("PASS phase2_costlog_venue_tag")


def test_client_order_id_is_stable_within_a_second():
    """STAGE10 §4.4: an ambiguous POST must be resolvable by querying the SAME
    id, so the id may not be random per attempt."""
    clock = lambda: 1_800_000_000.4
    a = order_id("rb", "AAAUSDT", clock)
    b = order_id("rb", "AAAUSDT", clock)
    assert a == b, (a, b)
    assert len(a) <= 36
    assert order_id("rb", "BBBUSDT", clock) != a
    print("PASS phase2_order_id_stable")
