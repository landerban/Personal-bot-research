"""
Stage 19b (§62) fixtures: the adopted construction's B.4 verification, the
G_ref invariant edge cases, and D_degenerate's five properties.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm.attribution import delta_gate, reporting_tuple, shadow_return  # noqa: E402
from rcm.factors import CovarianceModel  # noqa: E402
from rcm.gates import n_eff  # noqa: E402
from rcm.optimizer import G_CAP, epsilon_beta, solve  # noqa: E402
from rcm.statemachine import Calendar, classify, transition  # noqa: E402

SIGMA_D = 0.10 / np.sqrt(365)


def market(n=20, seed=21, beta_spread=0.15, idio=0.02):
    rng = np.random.default_rng(seed)
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    cov = CovarianceModel(b_btc=1.0 + beta_spread * rng.standard_normal(n),
                          b_eth=0.2 * rng.standard_normal(n),
                          sf_btc=0.03, sf_eth=0.015, d_idio=np.full(n, idio))
    return symbols, cov, np.full(n, 0.08), rng


# ------------------------------------------- B.4: the F-1 fixture, all seeds

@pytest.mark.parametrize("shape", ["linear", "steep"])
@pytest.mark.parametrize("seed", [21, 22, 23])
def test_adopted_construction_passes_f1_on_every_seed(shape, seed):
    """B.4: the adopted form must pass the ORIGINAL F-1 instances with every
    §60 frozen quantity unchanged (6, σ_target, ε_β, the 0.25 cap)."""
    symbols, cov, se, rng = market(seed=seed)
    n = len(symbols)
    if shape == "linear":
        mu = np.linspace(0.004, -0.004, n)
    else:
        base = np.linspace(1, -1, n)
        mu = np.sign(base) * base ** 2 * 0.004
    out = solve(symbols, mu, cov, se, se, SIGMA_D)
    nl = n_eff(np.where(out.w > 0, out.w, 0))
    ns = n_eff(np.where(out.w < 0, -out.w, 0))
    assert nl >= 6 - 1e-6 and ns >= 6 - 1e-6, (shape, seed, nl, ns)
    assert out.gross > 0.1, "the book must actually form, not vanish"
    print(f"PASS 19b_f1 {shape}/{seed} (N_eff {nl:.2f}L/{ns:.2f}S, "
          f"gross {out.gross:.3f})")


def test_no_name_is_ever_held_against_its_signal():
    """B.4 padding-analog for the sign-pre-assigned form: padding is
    impossible BY CONSTRUCTION, which shows up as sign agreement between
    every position and its signal — and zero-signal names hold nothing."""
    symbols, cov, se, rng = market(seed=31)
    mu = 0.003 * rng.standard_normal(len(symbols))
    mu[3] = 0.0
    mu[11] = 0.0
    out = solve(symbols, mu, cov, se, se, SIGMA_D)
    for i, w in enumerate(out.w):
        if mu[i] > 0:
            assert w >= -1e-12, f"{symbols[i]} long-signal held short"
        elif mu[i] < 0:
            assert w <= 1e-12, f"{symbols[i]} short-signal held long"
        else:
            assert w == 0.0, f"{symbols[i]} zero-signal holds a position"
    print("PASS 19b_sign_agreement")


def test_hedge_infeasibility_fixture_reports_the_stated_cost():
    """B.4 for sign-pre-assigned forms: when beta neutrality NEEDS holding a
    name against its signal (all long-signals high-beta, all short-signals
    low-beta), the restricted form cannot express meaningful gross. The
    formation consequence is REPORTED as the cost of the form, per §62.2 —
    not hidden, not fixed here."""
    n = 12
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    b = np.array([1.6] * 6 + [0.4] * 6)      # longs rich, shorts poor in beta
    cov = CovarianceModel(b_btc=b, b_eth=np.zeros(n), sf_btc=0.03,
                          sf_eth=0.015, d_idio=np.full(n, 0.02))
    se = np.full(n, 0.05)
    mu = np.array([0.004] * 6 + [-0.004] * 6)
    out = solve(symbols, mu, cov, se, se, SIGMA_D)
    eps = epsilon_beta(SIGMA_D, cov.sf_btc)
    # net beta per unit gross ≈ (1.6-0.4)/2 = 0.6 ⇒ gross ≤ ~eps/0.6 before
    # the SE term; the book must be TINY relative to an unconstrained one
    assert out.gross < eps / 0.6 + 1e-6, (
        f"gross {out.gross:.4f}: the chance constraint should throttle this")
    print(f"PASS 19b_hedge_cost (gross throttled to {out.gross:.4f}; "
          f"formation consequence of the adopted form, reported not hidden)")


def test_witness_style_instances_still_respect_every_frozen_quantity():
    """The flip must come from construction, not from a loosened constraint."""
    symbols, cov, se, rng = market(seed=22)
    mu = np.linspace(0.004, -0.004, len(symbols))
    out = solve(symbols, mu, cov, se, se, SIGMA_D)
    assert abs(np.sum(out.w)) <= 3e-8
    assert out.gross <= G_CAP + 1e-9
    assert np.max(np.abs(out.w)) <= 0.25 + 1e-9
    assert out.vol_model <= SIGMA_D + 1e-9
    assert out.beta_residual_btc <= epsilon_beta(SIGMA_D, cov.sf_btc) + 1e-6
    print("PASS 19b_frozen_quantities_unchanged")


# ---------------------------------------------------- §62.3: G_ref lifecycle

def test_g_ref_lifecycle_form_set_flatten_clear():
    w = np.array([0.05, 0.05, 0.05, -0.05, -0.05, -0.05])
    # before any formation: g_ref None, flat book, hold is a no-op
    t0 = transition(Calendar.GATE, None, np.zeros(6), 0.0, 0, g_ref=None)
    assert t0.action == "hold" and t0.g_ref_next is None
    # formation sets it
    tf = transition(Calendar.FORMED, w, np.zeros(6), 0.0, 3, g_ref=None)
    assert tf.g_ref_next == pytest.approx(0.30)
    # a formed-at-low-gross book may NEVER drift to G_cap (the 61.3.1 hole)
    grown = w * (2.9 / 0.30)                       # gross 2.9 < G_cap
    t = transition(Calendar.GATE, None, grown, 2.9, 0, g_ref=0.30)
    assert t.action == "rescale"
    assert np.sum(np.abs(t.w_next)) == pytest.approx(0.30), (
        "must return to G_ref, not be allowed up to G_cap")
    print("PASS 19b_g_ref_lifecycle")


# ------------------------------------------------ §62.4: D_degenerate's five

def test_degenerate_is_its_own_category_with_all_five_properties():
    # 1. counts in the calendar denominator, 2. not formed, 3. not a gate
    cal = [Calendar.FORMED] * 3 + [Calendar.DEGENERATE] * 2 + [Calendar.GATE]
    row = reporting_tuple(cal, 0.0, {})
    assert row["formation_rate"] == pytest.approx(3 / 6)
    assert row["gate_skip_rate"] == pytest.approx(1 / 6)
    assert row["degenerate_rate"] == pytest.approx(2 / 6)
    # 4. r_shadow = 0 exactly for a zero target — no NaN anywhere
    assert shadow_return(np.zeros(4), np.array([0.1, -0.2, 0.05, 0.3])) == 0.0
    # 5. EXCLUDED from delta_gate: planting extreme values on degenerate days
    #    must not move the statistic at all
    rng = np.random.default_rng(5)
    base_cal = ([Calendar.FORMED] * 40 + [Calendar.GATE] * 20
                + [Calendar.DEGENERATE] * 20)
    shadow = np.concatenate([rng.normal(0.001, 0.003, 40),
                             rng.normal(-0.001, 0.003, 20),
                             np.full(20, 99.0)])       # poison on degenerate
    d = delta_gate(shadow, base_cal, "hold/G_ref/M7")
    shadow2 = shadow.copy()
    shadow2[60:] = -99.0
    d2 = delta_gate(shadow2, base_cal, "hold/G_ref/M7")
    assert d.point == pytest.approx(d2.point), "degenerate days leaked in"
    assert d.n_formed == 40 and d.n_gate == 20
    # and the causal order: degenerate sits at the optimizer stage
    assert classify({"degenerate_target"}) is Calendar.DEGENERATE
    assert classify({"operational", "degenerate_target"}) is \
        Calendar.OPERATIONAL
    assert classify({"degenerate_target", "gates", "execution"}) is \
        Calendar.DEGENERATE
    print("PASS 19b_degenerate_five_properties")
