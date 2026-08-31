"""
Stage 20 §2–§4 (part 1): the seal, the no-real-data rule, the timeline,
the PIT calibration, funding cadence/observability, and the factor model.

Every test here fails if a structural claim of §59/§60/§60.11 is false.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm import seal as S  # noqa: E402
from rcm import timeline as TL  # noqa: E402
from rcm.factors import (  # noqa: E402
    AssetBetas, estimate_betas, orthogonalize_eth, residual_series,
)
from rcm.funding import FundingUnobservable, forecast  # noqa: E402
from rcm.momentum import CalibrationSet, calibrate  # noqa: E402

DAY = TL.DAY_MS


# ------------------------------------------------------------------ the seal

def test_seal_refuses_any_intersection_even_one_day():
    ok = lambda a, b: S.assert_range_allowed(a, b)
    # wholly before / after: allowed
    ok(S.SEAL_START_MS - 100 * DAY, S.SEAL_START_MS - 1)
    ok(S.SEAL_END_MS + 1, S.SEAL_END_MS + 100 * DAY)
    # touching by one day, inside, covering: all refused
    for a, b in [(S.SEAL_START_MS - 5 * DAY, S.SEAL_START_MS),
                 (S.SEAL_END_MS, S.SEAL_END_MS + 5 * DAY),
                 (S.SEAL_START_MS + DAY, S.SEAL_START_MS + 2 * DAY),
                 (S.SEAL_START_MS - DAY, S.SEAL_END_MS + DAY)]:
        with pytest.raises(S.SealViolation, match="SEALED"):
            ok(a, b)
    print("PASS rcm_seal_intersection")


def test_seal_unlock_needs_a_committed_ledger_entry():
    """A working-tree entry unlocks nothing: the marker must be in committed
    git history (the PITView principle — a guarantee, not a promise)."""
    tok = S.UnlockToken("test", created_ms=2_000_000_000_000)
    no_entry = lambda *a, **k: type("R", (), {"stdout": ""})()
    with pytest.raises(S.SealViolation, match="committed ledger history"):
        S.assert_range_allowed(S.SEAL_START_MS, S.SEAL_START_MS + DAY,
                               unlock=tok, _runner=no_entry)
    print("PASS rcm_seal_needs_committed_entry")


def test_seal_refuses_backdating():
    """An entry committed AFTER the request was created is back-dating and is
    refused; text in the entry claiming an earlier date is irrelevant because
    the check uses the COMMIT timestamp."""
    req_created = 1_900_000_000_000
    tok = S.UnlockToken("test", created_ms=req_created)
    committed_later = lambda *a, **k: type(
        "R", (), {"stdout": str((req_created + 60_000) // 1000) + "\n"})()
    with pytest.raises(S.SealViolation, match="no back-dating"):
        S.assert_range_allowed(S.SEAL_START_MS, S.SEAL_START_MS + DAY,
                               unlock=tok, _runner=committed_later)
    # and the proper ordering unlocks (loudly)
    committed_before = lambda *a, **k: type(
        "R", (), {"stdout": str((req_created - 60_000) // 1000) + "\n"})()
    S.assert_range_allowed(S.SEAL_START_MS, S.SEAL_START_MS + DAY,
                           unlock=tok, _runner=committed_before,
                           _now_ms=lambda: req_created + 1)
    print("PASS rcm_seal_no_backdating")


def test_no_rcm_module_can_reach_real_data():
    """Ground rule 1: import-level. No rcm module imports the production data
    client, the live trading client, or names the real store's database."""
    banned_imports = {"live.proddata", "live.client", "live.phase2",
                      "live.pitfeed"}
    for f in sorted((ROOT / "rcm").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n not in banned_imports, f"{f.name} imports {n}"
                assert not n.startswith("live."), f"{f.name} imports {n}"
        assert "xsmom.db" not in src, f"{f.name} names the real store"
        assert "fapi.binance" not in src, f"{f.name} names a venue"
    print("PASS rcm_no_real_data_imports")


# --------------------------------------------------------------- timeline

def test_calibration_boundary_pair_at_the_cutoff():
    """§60.11.2.3 verbatim: at a decision on day D, the outcome opened D−2
    (ending 00:01 D−1) is admissible; the one opened D−1 (ending 00:01 D) is
    NOT — it has not finished at the 00:00 cutoff."""
    D = 20_000 * DAY
    assert TL.outcome_admissible(D - 2 * DAY, D) is True
    assert TL.outcome_admissible(D - 1 * DAY, D) is False
    assert TL.newest_admissible_signal_day(D) == D - 2 * DAY
    # the interval arithmetic itself
    iv = TL.outcome_interval(D - 2 * DAY)
    assert iv.end_ms == TL.exec_time_ms(D - DAY)
    assert iv.end_ms <= TL.decision_cutoff_ms(D)
    print("PASS rcm_calibration_boundary")


def test_calibration_set_builder_rejects_unclosed_outcomes():
    """§60.11.2.4: an observation with outcome_end > decision_cutoff must not
    enter the pooled sample. The contaminated (D-1) cross-section carries a
    huge planted slope; if it leaked, b_hat would be far from the clean
    value."""
    D = 20_000 * DAY
    rng = np.random.default_rng(7)
    obs = []
    for k in range(2, 40):                     # admissible: D-2 .. D-39
        z = rng.normal(size=20)
        obs.append({"signal_day_ms": D - k * DAY, "z": z,
                    "eps_fwd": 0.001 * z + rng.normal(0, 0.01, 20)})
    z_bad = rng.normal(size=20)
    obs.append({"signal_day_ms": D - 1 * DAY, "z": z_bad,
                "eps_fwd": 100.0 * z_bad})     # the poison pill
    cs = CalibrationSet.build(obs, D)
    assert cs.n_cross_sections == 38, cs.n_cross_sections
    assert D - 1 * DAY not in cs.signal_days
    slope = calibrate(cs)
    assert abs(slope.b_hat) < 1.0, (
        f"b_hat {slope.b_hat}: the unclosed outcome leaked into the pool")
    print(f"PASS rcm_setbuilder_pit (b_hat {slope.b_hat:+.5f}, poison excluded)")


def test_holding_intervals_are_identical_by_construction():
    """r_shadow, r_actual_price and the calibration outcome share ONE
    function; a deliberate one-minute offset is detectable."""
    d = 21_000 * DAY
    a = TL.holding_interval(d)
    b = TL.outcome_interval(d)
    assert (a.start_ms, a.end_ms) == (b.start_ms, b.end_ms)
    assert a.start_ms == TL.day_start_ms(d) + 60_000
    offset = TL.HoldingInterval(a.start_ms + 60_000, a.end_ms + 60_000)
    assert (offset.start_ms, offset.end_ms) != (a.start_ms, a.end_ms)
    print("PASS rcm_one_horizon")


# ---------------------------------------------------------------- funding

class FakeView:
    """PITView-shaped: .as_of and .funding(symbol, since)."""

    def __init__(self, as_of, rows):
        self.as_of = as_of
        self._rows = rows

    def funding(self, symbol, since=None):
        return [r for r in self._rows if since is None or r[0] >= since]


def _settlements(end_ms, n, interval, rate=1e-4):
    ts = [(end_ms // interval) * interval - k * interval for k in range(n)]
    return sorted((t, rate) for t in ts)


def test_funding_uses_the_pit_inferred_cadence_not_a_constant():
    """§60.11.1: an 8h symbol expects 3 forward settlements per day, a 4h
    symbol 6 — from the SAME code path with no constant multiplier."""
    H8, H4 = 8 * 3_600_000, 4 * 3_600_000
    as_of = 22_000 * DAY
    t0, t1 = as_of + 60_000, as_of + DAY + 60_000
    v8 = FakeView(as_of, _settlements(as_of, 21, H8))
    f8 = forecast(v8, "AUSDT", t0, t1)
    assert f8.inferred_interval_ms == H8 and f8.n_forward == 3, f8
    v4 = FakeView(as_of, _settlements(as_of, 42, H4))
    f4 = forecast(v4, "BUSDT", t0, t1)
    assert f4.inferred_interval_ms == H4 and f4.n_forward == 6, f4
    assert f4.total == pytest.approx(2 * f8.total), "6 vs 3 settlements at equal rates"
    print("PASS rcm_funding_cadence")


def test_uncertified_funding_window_makes_the_candidate_unavailable():
    """A window with missing settlements is NOT certified: the candidate is
    unavailable — never zero-funded (the Gen-1 §2d 5 precedent)."""
    H8 = 8 * 3_600_000
    as_of = 22_000 * DAY
    rows = _settlements(as_of, 21, H8)
    holey = rows[:5] + rows[9:]                # 4 settlements missing
    with pytest.raises(FundingUnobservable, match="unavailable"):
        forecast(FakeView(as_of, holey), "CUSDT",
                 as_of + 60_000, as_of + DAY + 60_000)
    print("PASS rcm_funding_observability")


def test_funding_module_contains_no_new_tolerance():
    """§60.11.1: no minimum-count parameter, no magic tolerance. The only
    numeric constants permitted are the day length and the frozen 7."""
    src = (ROOT / "rcm" / "funding.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
              and not isinstance(n.value, bool)]
    allowed = {86_400_000, 7, 0, 1, 3_600_000}
    bad = [c for c in consts if c not in allowed]
    assert not bad, f"unexpected numeric constants in funding.py: {bad}"
    print("PASS rcm_funding_no_magic_numbers")


# ----------------------------------------------------------------- factors

def test_eth_orthogonalization_and_the_instability_it_prevents():
    rng = np.random.default_rng(11)
    T = 90
    btc = rng.normal(0, 0.03, T)
    eth = 0.9 * btc + rng.normal(0, 0.01, T)      # highly collinear
    perp = orthogonalize_eth(btc, eth)
    corr = np.corrcoef(btc, perp)[0, 1]
    assert abs(corr) < 1e-10, f"not orthogonal: corr {corr}"

    # why: raw two-column OLS coefficients swing wildly across half-samples
    r = 1.2 * btc + 0.5 * eth + rng.normal(0, 0.02, T)
    def raw_betas(sl):
        X = np.column_stack([np.ones(sl.stop - sl.start), btc[sl], eth[sl]])
        return np.linalg.lstsq(X, r[sl], rcond=None)[0][1:]
    b1, b2 = raw_betas(slice(0, 45)), raw_betas(slice(45, 90))
    raw_swing = float(np.max(np.abs(b1 - b2)))
    b = estimate_betas(r, btc, perp)
    assert b.se_btc > 0 and b.se_eth_perp > 0
    assert raw_swing > 0.3, "fixture should exhibit collinear instability"
    print(f"PASS rcm_orthogonalization (raw half-sample swing {raw_swing:.2f})")


def test_residuals_strip_the_factors():
    rng = np.random.default_rng(13)
    T = 90
    btc = rng.normal(0, 0.03, T)
    perp = rng.normal(0, 0.015, T)
    eps_true = rng.normal(0, 0.01, T)
    r = 0.002 + 1.5 * btc - 0.7 * perp + eps_true
    b = estimate_betas(r, btc, perp)
    eps = residual_series(r, btc, perp, b)
    assert abs(np.corrcoef(eps, btc)[0, 1]) < 0.05
    assert abs(np.corrcoef(eps, perp)[0, 1]) < 0.05
    assert b.beta_btc == pytest.approx(1.5, abs=0.15)
    print("PASS rcm_residuals")
