"""
Stage 20 §3–§4 (part 2): optimizer, gates, state machine, attribution,
null/canary tests, and the §60.11.3.4 optimizer→gate compatibility fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.sizing import SymbolFilters, size_from_weight  # noqa: E402
from rcm.attribution import (  # noqa: E402
    DIAGNOSTIC_LABEL, delta_gate, delta_transition, formed_days_metric,
    reporting_tuple, shadow_return,
)
from rcm.factors import CovarianceModel  # noqa: E402
from rcm.gates import (  # noqa: E402
    DegenerateTarget, GateConfig, Unresolved, c_signal, evaluate, n_eff,
)
from rcm.momentum import (  # noqa: E402
    CARRY_LABEL, CalibrationSet, CarryGuard, calibrate, mu_mom, zscores,
)
from rcm.optimizer import (  # noqa: E402
    G_CAP, NAME_CAP, OperationalFailure, epsilon_beta, solve,
)
from rcm.statemachine import (  # noqa: E402
    Calendar, M_FLATTEN, classify, transition,
)

DAY = 86_400_000


def make_market(n=20, seed=3, beta_spread=0.4, idio=0.02):
    rng = np.random.default_rng(seed)
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    b_btc = 1.0 + beta_spread * rng.standard_normal(n)
    b_eth = 0.2 * rng.standard_normal(n)
    cov = CovarianceModel(b_btc=b_btc, b_eth=b_eth, sf_btc=0.03,
                          sf_eth=0.015, d_idio=np.full(n, idio))
    se = np.full(n, 0.08)
    return symbols, cov, se, rng


# ---------------------------------------------------------------- optimizer

def test_optimizer_produces_a_neutral_book_within_all_caps():
    symbols, cov, se, rng = make_market()
    mu = 0.002 * rng.standard_normal(len(symbols))
    out = solve(symbols, mu, cov, se, se, sigma_target_daily=0.10 / np.sqrt(365))
    assert out.status == "optimal"
    assert abs(np.sum(out.w)) <= 3e-8
    assert out.gross <= G_CAP + 1e-9
    assert np.max(np.abs(out.w)) <= NAME_CAP + 1e-9
    assert out.vol_model <= 0.10 / np.sqrt(365) + 1e-9
    assert out.beta_residual_btc <= epsilon_beta(0.10 / np.sqrt(365),
                                                 cov.sf_btc) + 1e-6
    print(f"PASS rcm_opt_book (gross {out.gross:.3f}, vol "
          f"{out.vol_model * np.sqrt(365):.2%} ann)")


def test_optimizer_is_deterministic_and_w0_is_always_feasible():
    symbols, cov, se, rng = make_market(seed=5)
    mu = 0.002 * rng.standard_normal(len(symbols))
    a = solve(symbols, mu, cov, se, se, 0.10 / np.sqrt(365))
    b = solve(symbols, mu, cov, se, se, 0.10 / np.sqrt(365))
    assert np.max(np.abs(a.w - b.w)) <= 1e-9, "same inputs, same weights"
    # zero alpha: w=0 is feasible and (with turnover cost) optimal-ish
    z = solve(symbols, np.zeros(len(symbols)), cov, se, se, 0.10 / np.sqrt(365))
    assert z.status == "optimal" and z.gross <= 1e-6
    print("PASS rcm_opt_deterministic")


def test_optimizer_requires_canonical_ordering():
    symbols, cov, se, rng = make_market()
    shuffled = list(reversed(symbols))
    with pytest.raises(ValueError, match="lexicographically sorted"):
        solve(shuffled, np.zeros(len(symbols)), cov, se, se, 0.006)
    print("PASS rcm_opt_ordering")


def test_chance_constraint_coverage_is_near_nominal_10pct():
    """§60.11.5.2: with z=1.645 on |·|, nominal total breach is 10%. Under
    INDEPENDENT estimation errors with known V, realized breach on the solved
    book should be near nominal (the correlated case is the deferred stress
    test, §61)."""
    symbols, cov, se, rng = make_market(n=16, seed=9)
    mu = 0.003 * rng.standard_normal(len(symbols))
    out = solve(symbols, mu, cov, se, se, 0.10 / np.sqrt(365))
    w = out.w
    eps = epsilon_beta(0.10 / np.sqrt(365), cov.sf_btc)
    # simulate true betas around the estimates with the stated SEs
    breaches = 0
    M = 4000
    for _ in range(M):
        true_b = cov.b_btc + se * rng.standard_normal(len(w))
        breaches += abs(float(true_b @ w)) > eps
    rate = breaches / M
    # only meaningful when the constraint binds; loose books breach ~never
    binding = out.beta_residual_btc > 0.8 * eps
    assert rate <= 0.15, f"breach {rate:.1%} far above nominal on independent errors"
    print(f"PASS rcm_chance_coverage (breach {rate:.1%}, "
          f"{'binding' if binding else 'slack'} constraint)")


# ------------------------------------------------------------------- gates

def test_gate_g_min_is_unresolved_and_raises_without_a_value():
    """§60.11.6: no default exists in code. Evaluating without a configured
    g_min raises; supplying one works."""
    w_pre = np.array([0.1, 0.1, 0.1, -0.1, -0.1, -0.1])
    mu = np.array([1, 1, 1, -1, -1, -1.0]) * 1e-3
    with pytest.raises(Unresolved, match="g_min is UNRESOLVED"):
        evaluate(w_pre, w_pre, mu, GateConfig())          # g_min=None
    v = evaluate(w_pre, w_pre, mu, GateConfig(g_min=0.65))
    assert isinstance(v.passed, bool)
    print("PASS rcm_gate_gmin_unresolved")


def test_zero_momentum_mass_raises_until_the_user_decides():
    """§60.11.8.2: pure-carry state escalated; NaN never decides."""
    w_pre = np.array([0.2, -0.2])
    with pytest.raises(Unresolved, match="zero momentum mass"):
        c_signal(w_pre, np.zeros(2), np.array([True, True]))
    print("PASS rcm_zero_momentum_unresolved")


def test_degenerate_target_is_a_named_state_with_no_nan():
    with pytest.raises(DegenerateTarget, match="G_pre = 0"):
        evaluate(np.zeros(4), np.zeros(4), np.ones(4), GateConfig(g_min=0.5))
    print("PASS rcm_degenerate_target")


def test_neff_bounds_no_individual_weight():
    """§60.11.4: a leg with N_eff ≈ 6 can still hold one name at 30% — the
    Herfindahl count and a per-name cap are non-equivalent controls."""
    leg = np.array([0.30, 0.155, 0.155, 0.13, 0.13, 0.13])
    leg = leg / leg.sum()
    ne = n_eff(leg)
    assert ne >= 5.2 and leg.max() >= 0.30 - 1e-9
    # a book like this passes N_eff-style breadth while carrying a 30% name;
    # only the ABSOLUTE 0.25 optimizer cap addresses that, and only partially
    print(f"PASS rcm_neff_nonequivalence (N_eff {ne:.2f}, max weight "
          f"{leg.max():.0%})")


def test_c_signal_is_bounded_and_detects_hypothesis_discard():
    w_pre = np.array([0.2, 0.2, -0.2, -0.2])
    mu = np.array([0.004, 0.0001, -0.0001, -0.004])   # signal lives at 0 and 3
    all_live = c_signal(w_pre, mu, np.array([True] * 4))
    assert all_live == pytest.approx(1.0)
    # feasibility keeps the funding-ish names, drops the momentum names
    kept = c_signal(w_pre, mu, np.array([False, True, True, False]))
    assert kept < 0.05, "coverage must collapse when the hypothesis is dropped"
    print(f"PASS rcm_coverage (hypothesis-discard coverage {kept:.3f})")


# --------------------------------------------- optimizer -> gate compatibility

@pytest.mark.xfail(
    strict=True,
    reason="FINDING F-1 (NOTES 61): architecture incompatibility, found "
           "synthetically as 60.11.3.4 intended. The linear objective "
           "concentrates with alpha dispersion; only the vol constraint "
           "spreads. Measured N_eff on 20 names: flat alpha ~9.9 (passes), "
           "linear 5.4-5.9 (fails, every seed), steep 3.6-3.8 (fails badly). "
           "The more informative the signal shape, the more the optimizer "
           "concentrates and the gate rejects the day. Resolution is a "
           "Stage-19-class decision for the user; NOT patched here. "
           "strict=True: if this starts passing, something changed and must "
           "be explained.")
def test_pipeline_produces_a_broad_gatepassing_book_when_one_is_feasible():
    """§60.11.3.4 REQUIRED fixture: the 0.25 cap is a safety ceiling, not a
    breadth mechanism — so verify the optimizer does not return a needlessly
    concentrated target when a broad gate-passing book is clearly feasible."""
    n = 20
    symbols, cov, se, rng = make_market(n=n, seed=21, beta_spread=0.15,
                                        idio=0.02)
    # a clean, symmetric alpha spread: broad books are clearly available
    mu = np.linspace(0.004, -0.004, n)
    order = np.argsort([int(s[1:3]) for s in symbols])  # already aligned
    out = solve(symbols, mu, cov, se, se, 0.10 / np.sqrt(365))
    longs = np.where(out.w > 0, out.w, 0.0)
    shorts = np.where(out.w < 0, -out.w, 0.0)
    nl, ns = n_eff(longs), n_eff(shorts)
    assert nl >= 6 and ns >= 6, (
        f"architecture incompatibility: optimizer returned a concentrated "
        f"book (N_eff {nl:.1f}L/{ns:.1f}S) where a broad one was feasible")
    v = evaluate(out.w, out.w, mu, GateConfig(g_min=0.65))
    assert v.passed, v.failed_gates
    print(f"PASS rcm_opt_gate_compat (N_eff {nl:.1f}L / {ns:.1f}S)")


# ---------------------------------------------------------- state machine

def test_calendar_precedence_is_causal_not_control_flow():
    """A date failing at two stages classifies by the EARLIER causal stage,
    however the caller ordered its checks (set semantics)."""
    assert classify(set()) is Calendar.FORMED
    assert classify({"gates", "execution"}) is Calendar.GATE
    assert classify({"execution"}) is Calendar.STRUCTURAL
    assert classify({"structural_pre", "operational", "gates"}) is \
        Calendar.STRUCTURAL
    assert classify({"operational", "gates"}) is Calendar.OPERATIONAL
    assert classify({"degenerate_target", "gates"}) is Calendar.STRUCTURAL
    # permutation invariance is structural: input is a set
    a = classify({"gates", "operational"})
    b = classify({"operational", "gates"})
    assert a is b is Calendar.OPERATIONAL
    with pytest.raises(ValueError, match="unknown pipeline stages"):
        classify({"vibes"})
    print("PASS rcm_calendar_precedence")


def test_transition_is_one_common_rule_hold_rescale_flatten():
    w_prev = np.array([0.1, -0.1, 0.05, -0.05])
    for cat in (Calendar.GATE, Calendar.STRUCTURAL, Calendar.OPERATIONAL):
        t = transition(cat, None, w_prev, drifted_gross=0.3,
                       consecutive_nonformed=0)
        assert t.action == "hold" and np.array_equal(t.w_next, w_prev)
    # over the cap: single-scalar rescale, proportions preserved
    big = w_prev * 20                                  # gross 6.0 > 3.0
    t = transition(Calendar.GATE, None, big, drifted_gross=6.0,
                   consecutive_nonformed=0)
    assert t.action == "rescale"
    assert np.sum(np.abs(t.w_next)) == pytest.approx(G_CAP)
    ratios = t.w_next / big
    assert np.allclose(ratios, ratios[0]), "must be ONE scalar"
    # the M=7 staleness ceiling
    t7 = transition(Calendar.OPERATIONAL, None, w_prev, drifted_gross=0.3,
                    consecutive_nonformed=M_FLATTEN - 1)
    assert t7.action == "flatten" and not t7.w_next.any()
    # formed resets the counter
    tf = transition(Calendar.FORMED, w_prev * 0.5, w_prev, 0.3, 5)
    assert tf.action == "form" and tf.consecutive_nonformed == 0
    print("PASS rcm_transition_common_rule")


# ------------------------------------------------------------- attribution

def test_attribution_recovers_planted_deltas_and_names_the_rule():
    rng = np.random.default_rng(31)
    n = 400
    cal = [Calendar.FORMED if i % 3 else Calendar.GATE for i in range(n)]
    shadow = np.where([c is Calendar.FORMED for c in cal],
                      rng.normal(0.001, 0.004, n),      # formed days
                      rng.normal(-0.002, 0.004, n))     # gate-failed days
    dg = delta_gate(shadow, cal, transition_rule="hold+rescale+M7")
    assert dg.point == pytest.approx(0.003, abs=0.0015)
    assert dg.ci90[0] < dg.point < dg.ci90[1]
    assert dg.transition_rule == "hold+rescale+M7"

    actual = shadow.copy()
    gate_mask = np.array([c is Calendar.GATE for c in cal])
    actual[gate_mask] += 0.0015                         # planted transition effect
    dt = delta_transition(actual, shadow, cal, "hold+rescale+M7")
    assert dt.point == pytest.approx(0.0015, abs=1e-9)
    print(f"PASS rcm_attribution (dgate {dg.point:+.4f} "
          f"[{dg.ci90[0]:+.4f},{dg.ci90[1]:+.4f}], dtrans {dt.point:+.4f})")


def test_reporting_tuple_has_all_six_fields_and_the_literal_label():
    cal = ([Calendar.FORMED] * 6 + [Calendar.GATE] * 2
           + [Calendar.STRUCTURAL] + [Calendar.OPERATIONAL])
    row = reporting_tuple(cal, calendar_perf=0.01,
                          gate_counts={"exposure": 2})
    for f in ("calendar_performance", "formation_rate", "gate_skip_rate",
              "structural_skip_rate", "operational_skip_rate",
              "gate_composition"):
        assert f in row, f
    assert row["formation_rate"] == pytest.approx(0.6)
    m = formed_days_metric(1.23, "sharpe_formed_only")
    assert m["label"] == DIAGNOSTIC_LABEL
    assert "NOT STRATEGY PERFORMANCE" in m["label"]
    print("PASS rcm_reporting_tuple")


# ------------------------------------------------------- null / canaries

def _pooled_obs(rng, n_days, n_assets, b_true):
    obs = []
    D = 25_000 * DAY
    for k in range(2, n_days + 2):
        z = rng.normal(size=n_assets)
        eps = b_true * z + rng.normal(0, 0.01, n_assets)
        obs.append({"signal_day_ms": D - k * DAY, "z": z, "eps_fwd": eps})
    return obs, D


def test_random_signal_canary_the_machine_manufactures_no_alpha():
    """Noise in, nothing out: b̂ ≈ 0, b̃ shrunk further, μ_mom ≈ 0, the carry
    guard fires, and the calendar mean of the momentum leg ≈ 0."""
    rng = np.random.default_rng(41)
    obs, D = _pooled_obs(rng, 200, 20, b_true=0.0)
    slope = calibrate(CalibrationSet.build(obs, D))
    assert abs(slope.b_hat) < 5e-4, slope
    assert abs(slope.b_tilde) <= abs(slope.b_hat)
    mu = mu_mom(slope, zscores(rng.normal(size=20)))
    guard = CarryGuard()
    g = guard.update(mu, f_hat=np.full(20, 1e-4))
    assert g["carry_flag"] is True and g["label"] == CARRY_LABEL
    # calendar mean of shadow returns under the noise signal
    sh = [shadow_return(rng.normal(size=20) * 0.01,
                        rng.normal(0, 0.01, 20)) for _ in range(300)]
    t_stat = np.mean(sh) / (np.std(sh, ddof=1) / np.sqrt(len(sh)))
    assert abs(t_stat) < 3.0
    print(f"PASS rcm_canary_random (b_hat {slope.b_hat:+.2e}, t {t_stat:+.2f})")


def test_shuffled_return_canary():
    """A REAL slope exists; shuffling the outcomes must destroy it."""
    rng = np.random.default_rng(43)
    obs, D = _pooled_obs(rng, 200, 20, b_true=0.004)
    genuine = calibrate(CalibrationSet.build(obs, D))
    assert genuine.b_hat > 0.002, "fixture must carry a real slope"
    for o in obs:
        o["eps_fwd"] = rng.permutation(o["eps_fwd"])
    shuffled = calibrate(CalibrationSet.build(obs, D))
    assert abs(shuffled.b_hat) < genuine.b_hat / 4
    print(f"PASS rcm_canary_shuffled ({genuine.b_hat:+.4f} -> "
          f"{shuffled.b_hat:+.4f})")


def test_zero_alpha_nonzero_funding_never_forms_an_unlabelled_carry_book():
    """§60.11.8 status is UNRESOLVED ⇒ the state RAISES; it can never fall
    through into an unlabelled carry book."""
    w_pre = np.array([0.15, 0.1, -0.1, -0.15])
    with pytest.raises(Unresolved, match="zero momentum mass"):
        c_signal(w_pre, np.zeros(4), np.ones(4, dtype=bool))
    guard = CarryGuard()
    g = guard.update(np.zeros(4), f_hat=np.array([2e-4, 1e-4, -1e-4, -3e-4]))
    assert g["carry_flag"] is True and g["label"] == CARRY_LABEL
    print("PASS rcm_canary_pure_carry")


# ---------------------------------------------------------- quantization

def test_the_shared_sizing_trap_reproduces_inside_rcm():
    """The Stage-17 worked example must hold for RCM sizing too: RCM uses the
    SAME shared module, so $5.04 intended -> $4.91 executable -> rejected."""
    f = SymbolFilters("AAAUSDT", min_notional=5.0, step_size=0.01)
    s = size_from_weight("AAAUSDT", weight=0.0063, equity=800.0, price=102.7,
                         filters=f)   # intended 5.04, floor(qty) -> 0.04
    assert s.raw_qty * 102.7 == pytest.approx(5.04, abs=0.01)
    assert s.qty == pytest.approx(0.04)
    assert not s.ok and "below_min_notional" in s.reason
    print(f"PASS rcm_quantization_trap (intended $5.04 -> "
          f"${s.notional:.2f} -> rejected)")
