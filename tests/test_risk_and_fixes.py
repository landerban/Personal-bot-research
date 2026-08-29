"""
Tests for the risk layer (NOTES 46.2 criterion 5) and the four Phase-2 fixes
(criterion 4).

These exist because the criteria they serve were being reported as satisfied
while the code did not exist: status.json carried `kill_switch_armed: True`
and `drawdown: 0.0` as literals, and none of the four fixes was implemented.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live import risk as R  # noqa: E402
from live.fixes import (  # noqa: E402
    AmbiguousPost, check_atomicity, detect_stop_fills, place_order_idempotent,
    reconcile_funding, reconstruct_position_at, tracking_error,
)


# ------------------------------------------------------- the risk layer

def test_paper_equity_tracks_the_account_not_the_balance():
    """The account holds ~5000 of play money; the book is $800. Equity must
    follow the account's CHANGE, not its level."""
    st = R.RiskState(capital=800.0)
    st, ev = R.update(st, 1.0, 5000.0)            # first cycle: baseline
    assert ev is None
    assert st.paper_equity == 800.0
    assert st.reference_balance == 5000.0

    st, ev = R.update(st, 2.0, 5010.0, price_pnl=10.0)
    assert ev is None
    assert st.paper_equity == pytest.approx(810.0)
    assert st.drawdown == 0.0
    print("PASS risk_paper_equity_tracks_change")


def test_drawdown_is_measured_from_the_peak():
    st = R.RiskState(capital=800.0)
    st, _ = R.update(st, 1.0, 5000.0)
    st, _ = R.update(st, 2.0, 5100.0, price_pnl=100.0)     # 900, new peak
    st, _ = R.update(st, 3.0, 5010.0, price_pnl=-90.0)     # 810
    assert st.peak_equity == pytest.approx(900.0)
    assert st.drawdown == pytest.approx((900 - 810) / 900)
    assert not R.kill_switch_breached(st, 0.30)
    print(f"PASS risk_drawdown_from_peak ({st.drawdown:.2%})")


def test_kill_switch_trips_at_the_threshold_and_not_before():
    st = R.RiskState(capital=800.0)
    st, _ = R.update(st, 1.0, 5000.0)
    st, _ = R.update(st, 2.0, 5200.0, price_pnl=200.0)     # peak 1000
    st, _ = R.update(st, 3.0, 4990.0, price_pnl=-210.0)    # 790 -> 21% DD
    assert not R.kill_switch_breached(st, 0.30)
    st, _ = R.update(st, 4.0, 4900.0, price_pnl=-90.0)     # 700 -> 30% DD
    assert st.drawdown >= 0.30
    assert R.kill_switch_breached(st, 0.30)
    print(f"PASS risk_kill_switch_threshold ({st.drawdown:.2%})")


def test_a_testnet_reset_rebaselines_and_never_fires_the_kill_switch():
    """NOTES 46.5 -- the rule this project pre-registered precisely so a reset
    could not masquerade as a 100% drawdown."""
    st = R.RiskState(capital=800.0)
    st, _ = R.update(st, 1.0, 5000.0)
    st, _ = R.update(st, 2.0, 5050.0, price_pnl=50.0)       # 850
    held = st.paper_equity

    # the venue wipes the balance back to a round grant, with nothing to
    # explain it: not a loss, a reset
    st, ev = R.update(st, 3.0, 10000.0)
    assert ev is not None and ev["kind"] == "testnet_reset"
    assert st.paper_equity == pytest.approx(held), "series must be unbroken"
    assert st.drawdown == 0.0
    assert not R.kill_switch_breached(st, 0.30)
    assert len(st.resets) == 1

    # and trading continues from the new reference
    st, ev2 = R.update(st, 4.0, 10020.0, price_pnl=20.0)
    assert ev2 is None
    assert st.paper_equity == pytest.approx(held + 20.0)
    print("PASS risk_testnet_reset_rebaselines")


def test_a_real_loss_is_not_mistaken_for_a_reset():
    """The dangerous direction: misclassifying a loss as a reset would disarm
    the kill switch exactly when it is needed."""
    st = R.RiskState(capital=800.0)
    st, _ = R.update(st, 1.0, 5000.0)
    st, ev = R.update(st, 2.0, 4950.0, price_pnl=-50.0)     # explained loss
    assert ev is None, "an explained loss is not a reset"
    assert st.paper_equity == pytest.approx(750.0)
    assert st.drawdown == pytest.approx(50 / 800)
    print("PASS risk_loss_not_a_reset")


def test_risk_state_round_trips(tmp_path):
    st = R.RiskState(capital=800.0)
    st, _ = R.update(st, 1.0, 5000.0)
    st, _ = R.update(st, 2.0, 5100.0, price_pnl=100.0)
    st.save(tmp_path / "risk.json")
    back = R.RiskState.load(tmp_path / "risk.json")
    assert back.paper_equity == st.paper_equity
    assert back.peak_equity == st.peak_equity
    assert R.RiskState.load(tmp_path / "nope.json").paper_equity is None
    print("PASS risk_state_round_trip")


# ------------------------------------------------- fix 1: atomicity

def test_atomicity_passes_a_fully_filled_book():
    target = {"A": 0.05, "B": 0.05, "C": -0.05, "D": -0.05}
    betas = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}
    v = check_atomicity(target, dict(target), betas)
    assert v["needs_repair"] is False
    assert v["tracking_error"] == 0.0
    assert v["residual_beta"] == pytest.approx(0.0)
    print("PASS fix1_atomicity_clean")


def test_atomicity_catches_a_rejected_leg():
    """The failure the backtester cannot represent: one leg does not fill, so
    the book is directional and beta-exposed."""
    target = {"A": 0.05, "B": 0.05, "C": -0.05, "D": -0.05}
    actual = {"A": 0.05, "B": 0.05, "C": -0.05}          # D never filled
    betas = {k: 1.0 for k in target}
    v = check_atomicity(target, actual, betas)
    assert v["needs_repair"] is True
    assert v["missing_legs"] == ["D"]
    assert v["residual_beta"] == pytest.approx(0.05)
    assert v["tracking_error"] == pytest.approx(0.25)
    print(f"PASS fix1_atomicity_rejected_leg ({v['detail']})")


def test_atomicity_catches_a_beta_breach_even_when_sizes_look_fine():
    target = {"A": 0.10, "B": -0.10}
    actual = {"A": 0.10, "B": -0.10}
    betas = {"A": 3.0, "B": 0.5}          # very different betas
    v = check_atomicity(target, actual, betas)
    assert v["tracking_ok"] is True
    assert v["beta_ok"] is False, v
    assert v["needs_repair"] is True
    print(f"PASS fix1_atomicity_beta_breach (residual {v['residual_beta']:+.3f})")


def test_tracking_error_is_a_fraction_of_gross():
    assert tracking_error({"A": 0.1}, {"A": 0.1}) == 0.0
    assert tracking_error({"A": 0.1}, {}) == pytest.approx(1.0)
    assert tracking_error({}, {}) == 0.0
    print("PASS fix1_tracking_error")


# ------------------------------------------------- fix 2: stop cascade

def test_stop_cascade_detected_from_state_not_from_a_stream_event():
    """A stream gap must not become a missed cascade, so detection is from
    before/after position state."""
    orders = [{"symbol": "AAAUSDT", "type": "STOP_MARKET"},
              {"symbol": "BBBUSDT", "type": "STOP_MARKET"}]
    before = {"AAAUSDT": 1.0, "BBBUSDT": -2.0}
    after = {"BBBUSDT": -2.0}                 # AAA was stopped out
    assert detect_stop_fills(orders, before, after) == ["AAAUSDT"]

    # an ordinary rebalance that merely trims is NOT a cascade
    assert detect_stop_fills(orders, before, {"AAAUSDT": 0.9,
                                              "BBBUSDT": -2.0}) == []
    # no stops working -> nothing to detect
    assert detect_stop_fills([], before, {}) == []
    print("PASS fix2_stop_cascade_detection")


# ------------------------------------- fix 3: funding reconstruction

def test_funding_uses_the_position_held_at_the_settlement_instant():
    """The bug this fixes: reading the CURRENT book charges funding on a
    position that may have been opened AFTER the settlement. A rebalance ~15s
    past 00:00 is exactly that case."""
    settlement = 1_000_000
    fills = [
        {"ts": settlement - 60_000, "symbol": "AAAUSDT", "side": "BUY", "qty": 2.0},
        {"ts": settlement + 15_000, "symbol": "AAAUSDT", "side": "SELL", "qty": 5.0},
    ]
    assert reconstruct_position_at(fills, settlement, "AAAUSDT") == pytest.approx(2.0)
    # the current book would say -3.0, which is the wrong number
    assert sum(f["qty"] * (1 if f["side"] == "BUY" else -1)
               for f in fills) == pytest.approx(-3.0)

    assert reconstruct_position_at(fills, settlement - 120_000, "AAAUSDT") == 0.0
    assert reconstruct_position_at(fills, settlement, "OTHERUSDT") == 0.0
    print("PASS fix3_funding_reconstruction")


def test_funding_reconciles_to_the_exchange_within_a_cent():
    """NOTES 46.2 criterion 2."""
    recorded = [{"actual_amount": -0.0123}, {"actual_amount": 0.0456}]
    exch = [{"income": "-0.0123"}, {"income": "0.0456"}]
    out = reconcile_funding(recorded, exch)
    assert out["ok"] is True and out["drift"] == pytest.approx(0.0)

    drifted = reconcile_funding([{"actual_amount": 1.0}], [{"income": "0.5"}])
    assert drifted["ok"] is False
    assert drifted["drift"] == pytest.approx(0.5)
    print("PASS fix3_funding_reconciliation")


# ------------------------------------- fix 4: POST idempotency

class _Client:
    """Minimal stand-in that can fail a POST after accepting it."""

    def __init__(self, fail_posts=0, order_exists_after_fail=True):
        self.fail_posts = fail_posts
        self.order_exists_after_fail = order_exists_after_fail
        self.posts = 0
        self.queries = 0

    def place_order(self, **params):
        from live.client import NetworkError
        self.posts += 1
        if self.posts <= self.fail_posts:
            raise NetworkError("network: ReadTimeout")
        return {"orderId": 1, "status": "FILLED", "executedQty": "1"}

    def get_order(self, symbol, client_order_id):
        from live.client import ExchangeError
        self.queries += 1
        if self.order_exists_after_fail:
            return {"orderId": 99, "status": "FILLED",
                    "clientOrderId": client_order_id}
        raise ExchangeError("Order does not exist", code=-2013, status=400)


def test_ambiguous_post_queries_before_resubmitting_and_does_not_double():
    """THE failure: a timeout after a POST does not mean the order was
    rejected -- it means the RESPONSE was lost. Blind resubmission doubles the
    position."""
    c = _Client(fail_posts=1, order_exists_after_fail=True)
    out = place_order_idempotent(c, symbol="AAAUSDT", client_order_id="cid-1",
                                 side="BUY", type="MARKET", quantity="1",
                                 sleeper=lambda s: None)
    assert out["orderId"] == 99, "must return the EXISTING order"
    assert c.posts == 1, "must NOT resubmit once the order was found"
    assert c.queries == 1, "must query by client id first"
    print("PASS fix4_ambiguous_post_not_resubmitted")


def test_resubmits_only_when_the_query_proves_no_order_exists():
    c = _Client(fail_posts=1, order_exists_after_fail=False)
    out = place_order_idempotent(c, symbol="AAAUSDT", client_order_id="cid-2",
                                 side="BUY", type="MARKET", quantity="1",
                                 sleeper=lambda s: None)
    assert out["status"] == "FILLED"
    assert c.posts == 2, "a proven-absent order may be resubmitted"
    assert c.queries == 1
    print("PASS fix4_resubmit_when_proven_absent")


def test_a_filter_rejection_is_never_retried():
    from live.client import FilterRejected

    class Rejecting(_Client):
        def place_order(self, **params):
            self.posts += 1
            raise FilterRejected("notional too small", code=-4164, status=400)

    c = Rejecting()
    with pytest.raises(FilterRejected):
        place_order_idempotent(c, symbol="A", client_order_id="c",
                               side="BUY", type="MARKET", quantity="1",
                               sleeper=lambda s: None)
    assert c.posts == 1, "deterministic rejections must not be retried"
    print("PASS fix4_filter_rejection_not_retried")


def test_gives_up_loudly_rather_than_guessing():
    c = _Client(fail_posts=99, order_exists_after_fail=False)
    with pytest.raises(AmbiguousPost):
        place_order_idempotent(c, symbol="A", client_order_id="c",
                               side="BUY", type="MARKET", quantity="1",
                               max_attempts=2, sleeper=lambda s: None)
    print("PASS fix4_gives_up_loudly")


# ------------------------------------- criterion 5: the watchdog alert

def test_watchdog_fires_on_a_heartbeat_gap(tmp_path):
    """NOTES 46.2 criterion 5: 'a heartbeat gap test fires the alert'.

    The failure being defended against is a supervisor that is ALIVE but
    wedged, so the watchdog shares no code path with it -- it reads the
    timestamp inside the heartbeat file, not the file's mtime, because a
    wedged process that still touches the file must still trip it.
    """
    from live import watchdog as WD

    hb = tmp_path / "heartbeat"
    log = tmp_path / "paper_log.jsonl"
    fired = []

    def fake_flatten():
        fired.append(True)
        return {"closed": [], "cancelled": [], "remaining": {}}

    # fresh heartbeat -> silent
    hb.write_text(str(time.time()), encoding="utf-8")
    assert WD.check_once(hb, 300.0, log, flatten=fake_flatten) is None
    assert not fired

    # stale heartbeat -> flatten and record
    hb.write_text(str(time.time() - 600), encoding="utf-8")
    rec = WD.check_once(hb, 300.0, log, flatten=fake_flatten)
    assert rec is not None and rec["kind"] == "watchdog_trigger"
    assert fired == [True]

    # a MISSING heartbeat is also a gap -- absence is not innocence
    fired.clear()
    hb.unlink()
    assert WD.check_once(hb, 300.0, log, flatten=fake_flatten) is not None
    assert fired == [True]
    print("PASS criterion5_watchdog_fires_on_gap")


def test_watchdog_uses_the_timestamp_inside_the_file_not_mtime(tmp_path):
    """A wedged process that keeps touching the file must still trip."""
    from live import watchdog as WD

    hb = tmp_path / "heartbeat"
    hb.write_text(str(time.time() - 9999), encoding="utf-8")   # mtime is NOW
    age = WD.heartbeat_age_s(hb)
    assert age > 9000, age
    print("PASS criterion5_watchdog_reads_content_not_mtime")
