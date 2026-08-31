"""
Stage 19c (§62.8): shift invariance, the carry-regime formation fixture, and
the D_degenerate cause decomposition.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm.factors import CovarianceModel  # noqa: E402
from rcm.gates import Unresolved, c_signal, n_eff  # noqa: E402
from rcm.optimizer import degenerate_cause, solve  # noqa: E402

SIG = 0.10 / np.sqrt(365)


def market(n=20, seed=21, idio=0.02):
    rng = np.random.default_rng(seed)
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    cov = CovarianceModel(b_btc=1.0 + 0.15 * rng.standard_normal(n),
                          b_eth=0.2 * rng.standard_normal(n),
                          sf_btc=0.03, sf_eth=0.015, d_idio=np.full(n, idio))
    return symbols, cov, np.full(n, 0.08), rng


# ------------------------------------------------ §62.8.3.1 shift invariance

@pytest.mark.parametrize("c", [+0.006, -0.006])
@pytest.mark.parametrize("use_prev", [False, True])
def test_common_shift_invariance(c, use_prev):
    """(μ + c·1)ᵀw = μᵀw on the dollar-neutral feasible set, so the SOLVED
    book must be identical under a common shift — both signs of c, each large
    enough to flip several raw signs, and with a nonzero w_prev (the turnover
    term must also cancel).

    THE OLD RAW-SIGN PATH FAILED THIS (recorded in §62.8.3 before removal):
    c = +0.006 collapsed a 0.9011-gross book to 0.0000 with different
    membership. The failure record is the ledger's; this test pins the fix.
    """
    symbols, cov, se, rng = market()
    mu = np.linspace(0.004, -0.004, 20)
    assert np.sum(np.sign(mu) != np.sign(mu + c)) >= 5, \
        "shift must flip several raw signs or the fixture proves nothing"
    w_prev = 0.02 * np.sign(np.linspace(1, -1, 20)) if use_prev else None
    base = solve(symbols, mu, cov, se, se, SIG, w_prev=w_prev)
    shifted = solve(symbols, mu + c, cov, se, se, SIG, w_prev=w_prev)
    assert np.max(np.abs(shifted.w - base.w)) <= 1e-8
    assert np.array_equal(np.sign(shifted.w), np.sign(base.w))
    assert base.gross > 0.5, "the book must actually exist for this to bite"
    print(f"PASS 19c_shift_invariance c={c:+} prev={use_prev} "
          f"(max dw {np.max(np.abs(shifted.w - base.w)):.1e})")


# ---------------------------------------- §62.8.3.2 sign agreement, re-based

def test_sign_agreement_is_against_the_CENTERED_forecast():
    """Membership follows sign(μ̃), not sign(μ): with a shifted forecast the
    two disagree on several names and the book must follow μ̃."""
    symbols, cov, se, rng = market(seed=33)
    mu = np.linspace(0.004, -0.004, 20) + 0.003        # all but ~4 raw-positive
    mu_c = mu - mu.mean()
    out = solve(symbols, mu, cov, se, se, SIG)
    for i, w in enumerate(out.w):
        if mu_c[i] > 0:
            assert w >= -1e-12, f"{symbols[i]}: centered-long held short"
        elif mu_c[i] < 0:
            assert w <= 1e-12, f"{symbols[i]}: centered-short held long"
        else:
            assert w == 0.0
    # and the raw sign would have been wrong for the shorts
    assert np.sum((mu > 0) & (mu_c < 0)) >= 5
    print("PASS 19c_sign_agreement_centered")


# --------------------------------------- §62.8.3.3 the carry book CAN form

def test_carry_regime_book_forms_under_all_positive_funding():
    """μ_mom ≡ 0, every F̂ > 0 (varying): under raw sign every name was
    short-only and the CARRY REGIME book could not exist. Centered, the
    above-average-funding names go short (collecting it), below-average go
    long, and a broad book forms — with coverage still raising UNRESOLVED
    downstream because §60.11.8's user decision is pending."""
    symbols, cov, se, rng = market(seed=44)
    f_hat = np.linspace(0.0005, 0.0085, 20)            # all positive, varying
    mu_total = 0.0 - f_hat                             # μ_mom = 0
    out = solve(symbols, mu_total, cov, se, se, SIG)
    assert out.gross > 0.3, "the carry book must FORM (this was impossible " \
                            "under raw sign: every μ_total,i < 0)"
    shorts = out.w < 0
    assert np.all(f_hat[shorts] > f_hat.mean() - 1e-12), \
        "shorts must be the above-average-funding names"
    nl = n_eff(np.where(out.w > 0, out.w, 0))
    ns = n_eff(np.where(out.w < 0, -out.w, 0))
    assert nl >= 6 - 1e-6 and ns >= 6 - 1e-6
    # downstream: zero momentum mass still raises until the user decides
    with pytest.raises(Unresolved, match="zero momentum mass"):
        c_signal(out.w, np.zeros(20), np.abs(out.w) > 0)
    print(f"PASS 19c_carry_book_forms (gross {out.gross:.3f}, "
          f"N_eff {nl:.1f}L/{ns:.1f}S, downstream UNRESOLVED preserved)")


# ------------------------------------------- §62.8.4 the cause decomposition

def test_degenerate_cause_breadth():
    """The §62.8.4 example verbatim: 4 positive vs 26 negative centered names
    — the long leg cannot reach N_eff >= 6, the SOC zeroes it, neutrality
    zeroes the rest."""
    symbols, cov, se, rng = market(n=30, seed=55)
    mu = np.full(30, -0.001)
    mu[:4] = 0.02                       # centered: 4 strongly positive
    out = solve(symbols, mu, cov, se, se, SIG)
    assert out.gross <= 1e-6, "must be a zero book"
    cause = degenerate_cause(symbols, mu, cov, se, se, SIG)
    assert cause.startswith("constraint_interaction:breadth"), cause
    print(f"PASS 19c_cause_breadth ({cause})")


def test_degenerate_cause_chance():
    """Balanced membership but the betas are unidentifiable: the chance
    constraint THROTTLES — it is bounded, not conic, so it can never force an
    exactly-zero book on its own; a moderate throttle leaves a micro-book
    (gate territory, not D_degenerate). Only when the throttle falls below
    solver precision does the zero-clean land the day in D_degenerate — and
    then the diagnostic re-solve without the chance constraints names it."""
    n = 20
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    b = np.array([1.8] * 10 + [0.2] * 10)
    cov = CovarianceModel(b_btc=b, b_eth=np.zeros(n), sf_btc=0.03,
                          sf_eth=0.015, d_idio=np.full(n, 0.02))
    mu = np.array([0.004] * 10 + [-0.004] * 10)
    # moderate SE: throttled but NONZERO — this day is not degenerate at all
    mid = solve(symbols, mu, cov, np.full(n, 1e4), np.full(n, 1e4), SIG)
    assert 0.0 < mid.gross < 1e-3, mid.gross
    # SE so large the throttle is sub-precision: post-clean exact zero
    se = np.full(n, 1e6)
    out = solve(symbols, mu, cov, se, se, SIG)
    assert out.gross == 0.0, f"gross {out.gross}: must clean to exactly zero"
    cause = degenerate_cause(symbols, mu, cov, se, se, SIG)
    assert cause == "constraint_interaction:chance", cause
    print(f"PASS 19c_cause_chance (micro-book {mid.gross:.1e} at se=1e4; "
          f"zero at se=1e6 -> {cause})")


def test_degenerate_cause_no_trade():
    """A centered forecast too small to beat the frozen cost term: zero book,
    and the cause is genuinely economic."""
    symbols, cov, se, rng = market(seed=66)
    mu = np.linspace(1e-6, -1e-6, 20)                   # tiny vs eta=1e-3
    out = solve(symbols, mu, cov, se, se, SIG)
    assert out.gross <= 1e-8
    cause = degenerate_cause(symbols, mu, cov, se, se, SIG)
    assert cause == "no_trade", cause
    print(f"PASS 19c_cause_no_trade ({cause})")
