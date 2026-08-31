"""
Stage 22 Part II (§63.6): the residual factor covariance estimator —
every II.2 fixture. Deterministic claims are asserted; informational
diagnostics are REPORTED with no frequency or exact-K criterion, per the
delegates' rulings (the MP edge is an asymptotic reference, and population
spikes vs sample eigenvalues are different objects).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm.factors import CovarianceModel  # noqa: E402
from rcm.gates import GateConfig, IntegrityFailure, evaluate  # noqa: E402
from rcm.optimizer import SOLVER_TOL, Z_CHANCE, epsilon_beta, solve  # noqa: E402
from rcm.rescov import (  # noqa: E402
    FullResidualCovarianceModel, M_DOF, ResCov, apply_remainder_policy,
    estimate, mp_edge_raw, psd_sqrt,
)

SIG = 0.10 / np.sqrt(365)


def equicorr(n: int, rho: float) -> np.ndarray:
    return (1 - rho) * np.eye(n) + rho * np.ones((n, n))


def corr_from_resid(e: np.ndarray) -> np.ndarray:
    return np.corrcoef(e)


# ------------------------------------------------------------ deterministic

def test_marginals_preserved_exactly():
    rng = np.random.default_rng(1)
    c = corr_from_resid(rng.standard_normal((25, 90))
                        + 0.8 * rng.standard_normal((1, 90)))
    d = rng.uniform(1e-4, 9e-4, 25)
    res = estimate(c, d)
    assert res.k >= 1, "fixture must retain at least one spike"
    assert np.max(np.abs(np.diag(res.omega) - d)) < 1e-12
    assert np.max(np.abs(np.diag(res.c_rcm) - 1.0)) < 1e-12
    print(f"PASS 22_marginals (K={res.k})")


def test_psd_on_adversarial_rank_deficient_input():
    """N = 120 > 87: the sample correlation is rank-deficient BY
    CONSTRUCTION; Omega-hat must still be PSD."""
    rng = np.random.default_rng(2)
    e = rng.standard_normal((120, 90)) + 1.2 * rng.standard_normal((1, 90))
    c = corr_from_resid(e)
    d = rng.uniform(1e-4, 9e-4, 120)
    res = estimate(c, d)
    lam_min = float(np.linalg.eigvalsh(res.omega).min())
    assert lam_min >= -1e-10, lam_min
    assert res.k >= 1
    print(f"PASS 22_psd_adversarial (K={res.k}, lam_min={lam_min:.1e})")


def test_k0_boundary_returns_d_exactly():
    """All eigenvalues below the edge ⇒ Ω̂ = D bit-for-bit — the diagonal
    model is the automatic boundary case, no fallback rule."""
    n = 30
    c = equicorr(n, 0.04)              # top eigenvalue 2.16 < edge 2.52
    assert 1 + (n - 1) * 0.04 < mp_edge_raw(n)
    d = np.linspace(1e-4, 9e-4, n)
    res = estimate(c, d)
    assert res.k == 0
    assert np.array_equal(res.omega, np.diag(d))
    assert np.array_equal(res.c_rcm, np.eye(n))
    print("PASS 22_k0_boundary_exact")


def test_rank_rule_deterministic_two_known_spikes():
    """Two equicorrelation blocks with EXACTLY known eigenvalues bracketing
    the edge: K must be exactly 2 and L exactly those components."""
    n, nb = 30, 15
    edge = mp_edge_raw(n)                            # 2.5192
    c = np.zeros((n, n))
    c[:nb, :nb] = equicorr(nb, 0.5)                  # spike 1+14*0.5  = 8.0
    c[nb:, nb:] = equicorr(nb, 0.35)                 # spike 1+14*0.35 = 5.9
    assert 8.0 > edge and 5.9 > edge and 0.65 < edge
    d = np.full(n, 4e-4)
    res = estimate(c, d)
    assert res.k == 2
    assert res.retained_eigenvalues[0] == pytest.approx(8.0, abs=1e-10)
    assert res.retained_eigenvalues[1] == pytest.approx(5.9, abs=1e-10)
    v1 = np.zeros(n); v1[:nb] = 1 / np.sqrt(nb)
    v2 = np.zeros(n); v2[nb:] = 1 / np.sqrt(nb)
    expected_l = 8.0 * np.outer(v1, v1) + 5.9 * np.outer(v2, v2)
    assert np.max(np.abs(res.low_rank - expected_l)) < 1e-9
    print("PASS 22_rank_rule_deterministic (K=2 exact)")


def test_no_shrinkage_retained_values_bit_for_bit():
    rng = np.random.default_rng(3)
    e = rng.standard_normal((20, 90)) + rng.standard_normal((1, 90))
    c = corr_from_resid(e)
    res = estimate(c, np.full(20, 4e-4))
    csym = (np.asarray(c, float) + np.asarray(c, float).T) / 2.0
    lam = np.linalg.eigh(csym)[0][::-1]
    assert res.k >= 1
    for j in range(res.k):
        assert res.retained_eigenvalues[j] == lam[j], "shrinkage detected"
    print(f"PASS 22_raw_spikes (K={res.k}, bit-identical)")


def test_floating_point_policy_branchpoints():
    """The frozen remainder policy at both branch points, plus the
    fail-closed end-to-end path and symmetrization."""
    r, corr = apply_remainder_policy(np.array([0.3, -SOLVER_TOL / 2, 0.0]))
    assert r[1] == 0.0 and corr == SOLVER_TOL / 2
    with pytest.raises(IntegrityFailure, match="integrity"):
        apply_remainder_policy(np.array([0.3, -2 * SOLVER_TOL]))
    # garbage in -> fail closed: a "correlation" with diag(L) far above 1
    with pytest.raises(IntegrityFailure):
        estimate(np.diag([4.0] + [1.0] * 29), np.full(30, 4e-4))
    # near-rank-deficiency (an exactly duplicated name) survives with the
    # policy applied and the correction recorded
    rng = np.random.default_rng(4)
    e = rng.standard_normal((20, 90)) + rng.standard_normal((1, 90))
    e[1] = e[0]
    res = estimate(corr_from_resid(e), np.full(20, 4e-4))
    assert np.all(np.diag(res.c_rcm) >= 0) and res.r_correction_max >= 0.0
    # symmetrization: an asymmetric perturbation equals its symmetrized twin
    c = corr_from_resid(e)
    c_asym = c.copy(); c_asym[0, 1] += 1e-9
    c_sym = (c_asym + c_asym.T) / 2
    a, b = estimate(c_asym, np.full(20, 4e-4)), estimate(c_sym, np.full(20, 4e-4))
    assert np.array_equal(a.omega, b.omega)
    print(f"PASS 22_fp_policy (max correction {res.r_correction_max:.1e})")


def test_universe_ordering_estimator_cannot_see_downstream():
    """§63.6.4: estimation runs before any score/weight/gate. The API has
    nothing to condition on, and output is bit-identical however the
    downstream context changes around it."""
    assert list(inspect.signature(estimate).parameters) == ["corr",
                                                           "resid_var"]
    rng = np.random.default_rng(5)
    e = rng.standard_normal((20, 90)) + rng.standard_normal((1, 90))
    c, d = corr_from_resid(e), np.full(20, 4e-4)
    first = estimate(c, d)
    # "change" momentum scores, weights, and gate outcomes in between
    _ = np.linspace(0.004, -0.004, 20)
    _ = np.sign(rng.standard_normal(20)) * 0.05
    second = estimate(c, d)
    assert np.array_equal(first.omega, second.omega)
    assert first.k == second.k
    print("PASS 22_ordering_invariance")


def test_determinism_and_sign_convention():
    rng = np.random.default_rng(6)
    e = rng.standard_normal((20, 90)) + rng.standard_normal((1, 90))
    c, d = corr_from_resid(e), np.full(20, 4e-4)
    a, b = estimate(c, d), estimate(c, d)
    assert np.array_equal(a.omega, b.omega)
    for j in range(a.k):
        q = a.retained_vectors[:, j]
        assert q[int(np.argmax(np.abs(q)))] > 0, "sign convention violated"
    print("PASS 22_determinism_sign")


# -------------------------------------------------- neutrality, corrected

def test_neutrality_corrected_both_fixtures():
    """The TRUE claim: a covariance-space loading a = c·1 contributes zero
    to wᵀ(aaᵀ)w for exactly dollar-neutral w. NOT asserted for the full
    marginal-preserving Ω̂. Second fixture: q ∝ 1 in CORRELATION space
    with heteroskedastic D gives a = D^{1/2}q ∦ 1 — empirical modes are
    not annihilated by neutrality, which is why they belong in the model."""
    n = 8
    a = 0.7 * np.ones(n)
    for w in (np.array([.25, -.25, .125, -.125, .0625, -.0625, .5, -.5]),
              np.array([.5, .25, -.75, .125, -.125, .25, -.25, 0.])):
        assert float(np.sum(w)) == 0.0, "fixture must be exactly neutral"
        assert (a @ w) ** 2 == 0.0
    d = np.linspace(1e-4, 9e-4, n)                  # heteroskedastic
    q1 = np.ones(n) / np.sqrt(n)
    a2 = np.sqrt(d) * q1
    w = np.array([.25, -.25, .125, -.125, .0625, -.0625, .5, -.5])
    rel = abs(a2 @ w) / (np.linalg.norm(a2) * np.linalg.norm(w))
    assert rel > 0.01, f"a = D^{{1/2}}q must NOT be annihilated (rel {rel:.4f})"
    print(f"PASS 22_neutrality_corrected (relative projection {rel:.3f})")


# ------------------------------------------------------- chance constraint

def test_chance_se_reduces_exactly_at_k0_and_exceeds_with_a_mode():
    n = 12
    d = np.linspace(2e-4, 8e-4, n)
    g = 12.5
    w = np.linspace(0.2, -0.2, n)
    # K = 0: Omega = D and the independent-error formula is reproduced
    model0 = FullResidualCovarianceModel.create(
        np.ones(n), np.zeros(n), 0.03, 0.015, np.diag(d), g, g)
    se_full = model0.se_chance(w, "btc")
    se_indep = float(np.linalg.norm(np.sqrt(g * d) * w))
    assert se_full == pytest.approx(se_indep, rel=1e-12)
    # a planted mode aligned with w strictly exceeds it
    res = estimate(equicorr(n, 0.5), d)
    assert res.k == 1
    model1 = FullResidualCovarianceModel.create(
        np.ones(n), np.zeros(n), 0.03, 0.015, res.omega, g, g)
    w_aligned = np.abs(w) + 0.05
    assert model1.se_chance(w_aligned, "btc") > float(
        np.linalg.norm(np.sqrt(g * d) * w_aligned)) * 1.05
    print("PASS 22_chance_se (exact at K=0, strict excess with a mode)")


# --------------------------------------------------- informational reports

def test_null_behaviour_informational_no_criterion():
    """Spherical residuals, 90 obs: REPORT the K distribution. No pass/fail
    frequency criterion — the MP edge is an asymptotic reference."""
    rng = np.random.default_rng(7)
    ks = []
    for _ in range(150):
        c = corr_from_resid(rng.standard_normal((40, 90)))
        res = estimate(c, np.full(40, 4e-4))
        assert res.k >= 0
        ks.append(res.k)
    ks = np.array(ks)
    print(f"REPORT 22_null_behaviour: P(K=0)={np.mean(ks == 0):.2f}, "
          f"E[K]={ks.mean():.2f}, max K={ks.max()} "
          f"(informational; no criterion)")


def test_rank_recovery_stochastic_informational():
    """Known two-spike population, 90 sampled observations: REPORT
    detection and loading alignment. No exact-K acceptance criterion."""
    rng = np.random.default_rng(8)
    n, nb = 30, 15
    c_pop = np.zeros((n, n))
    c_pop[:nb, :nb] = equicorr(nb, 0.5)
    c_pop[nb:, nb:] = equicorr(nb, 0.35)
    chol = np.linalg.cholesky(c_pop + 1e-12 * np.eye(n))
    v1 = np.zeros(n); v1[:nb] = 1 / np.sqrt(nb)
    ks, aligns = [], []
    for _ in range(100):
        e = chol @ rng.standard_normal((n, 90))
        res = estimate(corr_from_resid(e), np.full(n, 4e-4))
        ks.append(res.k)
        if res.k >= 1:
            aligns.append(abs(float(res.retained_vectors[:, 0] @ v1)))
    ks = np.array(ks)
    print(f"REPORT 22_rank_recovery: K counts "
          f"{dict(zip(*np.unique(ks, return_counts=True)))}, "
          f"median |q1.v1|={np.median(aligns):.3f} "
          f"(informational; population vs sample eigenvalues differ)")


# ------------------------------------------ downstream: one risk model

def test_vret_and_gates_under_omega_single_object():
    """V_ret and the optimizer share the one full-covariance object; the
    off-diagonal structure demonstrably enters V_ret."""
    n = 20
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    rng = np.random.default_rng(11)
    res = estimate(corr_from_resid(
        rng.standard_normal((n, 90)) + 0.7 * rng.standard_normal((1, 90))),
        np.full(n, 4e-4))
    assert res.k >= 1

    calls = []

    class WitnessFull(FullResidualCovarianceModel):
        def portfolio_vol(self, w):
            calls.append(id(self))
            return FullResidualCovarianceModel.portfolio_vol(self, w)

    base = FullResidualCovarianceModel.create(
        1.0 + 0.15 * rng.standard_normal(n), 0.2 * rng.standard_normal(n),
        0.03, 0.015, res.omega, 12.5, 50.0)
    cov = WitnessFull(**{f: getattr(base, f) for f in (
        "b_btc", "b_eth", "sf_btc", "sf_eth", "omega", "omega_sqrt",
        "g_btc", "g_eth")})
    mu = np.linspace(0.004, -0.004, n)
    out = solve(symbols, mu, cov, None, None, SIG)
    n_before = len(calls)
    w_real = out.w.copy()
    w_real[np.abs(w_real) < np.percentile(np.abs(w_real[w_real != 0]), 30)] = 0
    v = evaluate(out.w, w_real, mu, GateConfig(), cov, SIG)
    assert len(calls) == n_before + 2 and set(calls) == {id(cov)}
    # the correlation structure must actually enter V_ret: recompute under
    # a diagonal-only twin and require a different answer
    diag_twin = CovarianceModel(b_btc=cov.b_btc, b_eth=cov.b_eth,
                                sf_btc=0.03, sf_eth=0.015,
                                d_idio=np.sqrt(np.diag(res.omega)))
    v_diag = evaluate(out.w, w_real, mu, GateConfig(), diag_twin, SIG)
    assert v.v_ret != pytest.approx(v_diag.v_ret, rel=1e-6), \
        "V_ret ignored the off-diagonal structure"
    print(f"PASS 22_one_risk_model (V_ret {v.v_ret:.4f} full vs "
          f"{v_diag.v_ret:.4f} diagonal twin)")


def test_se_arrays_and_full_model_are_mutually_exclusive():
    n = 8
    cov = FullResidualCovarianceModel.create(
        np.ones(n), np.zeros(n), 0.03, 0.015, np.diag(np.full(n, 4e-4)),
        12.5, 50.0)
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    with pytest.raises(ValueError, match="its own SE geometry"):
        solve(symbols, np.zeros(n), cov, np.full(n, 0.08), np.full(n, 0.08),
              SIG)
    dcov = CovarianceModel(b_btc=np.ones(n), b_eth=np.zeros(n), sf_btc=0.03,
                           sf_eth=0.015, d_idio=np.full(n, 0.02))
    with pytest.raises(ValueError, match="requires per-asset SE"):
        solve(symbols, np.zeros(n), dcov, None, None, SIG)
    print("PASS 22_model_se_exclusivity")


# --------------------------- F-1 / §62.8 fixtures: invariants, not outcomes

@pytest.mark.parametrize("shape", ["linear", "steep"])
@pytest.mark.parametrize("seed", [21, 22, 23])
def test_f1_construction_invariants_hold_under_omega(shape, seed):
    """Re-run the six F-1 instances under Ω̂. Every frozen construction
    invariant must hold; numerical outcomes (gross, N_eff values, V_ret)
    are PERMITTED to change and are recorded, not compared."""
    n = 20
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    rng = np.random.default_rng(seed)
    b_btc = 1.0 + 0.15 * rng.standard_normal(n)
    b_eth = 0.2 * rng.standard_normal(n)
    # residual data seeded FROM the frozen F-1 seed (I.1 correction 7)
    rng_e = np.random.default_rng(10_000 + seed)
    e = (0.6 * rng_e.standard_normal((n, 1)) @ rng_e.standard_normal((1, 90))
         + rng_e.standard_normal((n, 90)))
    res = estimate(np.corrcoef(e), np.full(n, 4e-4))
    # a plausible common design for the g's, same seed lineage
    x = np.column_stack([np.ones(90),
                         0.03 * rng_e.standard_normal(90),
                         0.015 * rng_e.standard_normal(90)])
    xtx_inv = np.linalg.inv(x.T @ x)
    cov = FullResidualCovarianceModel.create(
        b_btc, b_eth, 0.03, 0.015, res.omega,
        float(xtx_inv[1, 1]), float(xtx_inv[2, 2]))
    if shape == "linear":
        mu = np.linspace(0.004, -0.004, n)
    else:
        base = np.linspace(1, -1, n)
        mu = np.sign(base) * base ** 2 * 0.004
    out = solve(symbols, mu, cov, None, None, SIG)
    # --- the frozen invariants ---
    assert abs(float(np.sum(out.w))) <= 3e-8                 # 1ᵀw = 0
    mu_c = mu - mu.mean()                                    # §62.8 membership
    for i, w in enumerate(out.w):
        if mu_c[i] > 0:
            assert w >= -1e-12
        elif mu_c[i] < 0:
            assert w <= 1e-12
        else:
            assert w == 0.0
    longs = np.where(out.w > 0, out.w, 0.0)
    shorts = np.where(out.w < 0, -out.w, 0.0)
    from rcm.gates import n_eff
    for leg in (longs, shorts):
        if leg.sum() > 0:
            assert n_eff(leg) >= 6 - 1e-6                    # per-leg SOC
    assert np.max(np.abs(out.w)) <= 0.25 + 1e-9              # name cap
    assert cov.portfolio_vol(out.w) <= SIG * (1 + 1e-6)      # Σ_model ceiling
    eps = epsilon_beta(SIG, 0.03)
    se_term = Z_CHANCE * cov.se_chance(out.w, "btc")
    assert abs(float(b_btc @ out.w)) + se_term <= eps + 1e-6  # new-SE chance
    # common-shift invariance under the full model
    shifted = solve(symbols, mu + 0.006, cov, None, None, SIG)
    assert np.max(np.abs(shifted.w - out.w)) <= 1e-8
    print(f"RECORD 22_f1_under_omega {shape}/{seed}: K={res.k} "
          f"gross={out.gross:.3f} (outcome recorded, not compared)")


def test_f1_books_form_on_most_instances_under_omega():
    """Fixture-strength check only: the invariants test must not be
    vacuously green on six zero books."""
    formed = 0
    for seed in (21, 22, 23):
        n = 20
        symbols = sorted(f"A{k:02d}USDT" for k in range(n))
        rng = np.random.default_rng(seed)
        b_btc = 1.0 + 0.15 * rng.standard_normal(n)
        b_eth = 0.2 * rng.standard_normal(n)
        rng_e = np.random.default_rng(10_000 + seed)
        e = (0.6 * rng_e.standard_normal((n, 1))
             @ rng_e.standard_normal((1, 90))
             + rng_e.standard_normal((n, 90)))
        res = estimate(np.corrcoef(e), np.full(n, 4e-4))
        x = np.column_stack([np.ones(90),
                             0.03 * rng_e.standard_normal(90),
                             0.015 * rng_e.standard_normal(90)])
        xtx_inv = np.linalg.inv(x.T @ x)
        cov = FullResidualCovarianceModel.create(
            b_btc, b_eth, 0.03, 0.015, res.omega,
            float(xtx_inv[1, 1]), float(xtx_inv[2, 2]))
        mu = np.linspace(0.004, -0.004, n)
        if solve(symbols, mu, cov, None, None, SIG).gross > 0.05:
            formed += 1
    assert formed >= 2, f"only {formed}/3 books formed — fixture too weak"
    print(f"PASS 22_f1_fixture_strength ({formed}/3 linear books form)")


# ------------------------------------------------------------- reporting

def test_daily_report_carries_k_and_share_beside_null_references():
    rng = np.random.default_rng(9)
    e = rng.standard_normal((25, 90)) + rng.standard_normal((1, 90))
    res = estimate(np.corrcoef(e), np.full(25, 4e-4))
    rep = res.report()
    for key in ("K_t", "lambda1_share", "mp_edge_raw", "mp_edge_share",
                "r_correction_max"):
        assert key in rep
    assert rep["mp_edge_share"] == pytest.approx(rep["mp_edge_raw"] / 25)
    assert rep["K_t"] == res.k and 0 < rep["lambda1_share"] < 1
    print(f"PASS 22_daily_report ({ {k: round(v, 4) if isinstance(v, float) else v for k, v in rep.items()} })")
