"""
§60.7 + §60.11.3 + §62.2: the RCM optimizer — an SOCP with two knobs
eliminated, and breadth as part of CONSTRUCTION (Stage 19b, adopted after
FINDING F-1 was proven by a same-gross witness).

    max_w   μᵀw − η·‖w − w_prev‖₁
    s.t.    Σ w_i = 0                              exact dollar neutrality
            portfolio_vol(w) ≤ σ_daily             vol target as a CONSTRAINT
            |β̂_kᵀw| + z·‖SE_k ∘ w‖₂ ≤ ε_β,k       chance neutrality, k ∈ {BTC, ETH⊥}
            ‖w‖₁ ≤ G_cap                           the 3.0 backstop
            |w_i| ≤ G_cap / 12 = 0.25              hard safety cap (§60.11.3.2)

γ does not exist (vol is a constraint). η is not free — it IS the frozen
10 bps per-side cost stack. w = 0 is always feasible, so the problem is never
infeasible: a degenerate market produces a zero book, which is D_degenerate
(§62.4) — an economic decision, not a failure.

THE §62.2 CONSTRUCTION, CORRECTED BY §62.8 (shift invariance):
  * membership by RAW sign(μ) was a bug: the exactly dollar-neutral
    objective satisfies (μ + c·1)ᵀw = μᵀw, so the problem is invariant to a
    common shift while raw sign is not — a +0.006 shift collapsed a
    0.90-gross book to zero (recorded in §62.8.3). Membership is therefore
    assigned by the CENTERED forecast μ̃ = P·μ_total, P = I − (1/N)·1·1ᵀ —
    the unique projection implied by the neutrality constraint itself, not
    a preference among centers. Computed ONCE over the eligible set passed
    in; never recomputed after removing centered-sign names (§62.8.2).
  * w_i ≥ 0 where μ̃_i > 0, w_i ≤ 0 where μ̃_i < 0, w_i = 0 where μ̃_i = 0.
    No split variables exist, so padding is impossible by construction; a
    name is held only on the side of its cross-sectional total expected-
    return advantage, momentum and funding combined (§62.8.5 wording).
  * the objective also uses μ̃: identical on the feasible set (the discarded
    component is invisible to any dollar-neutral w) and numerically
    shift-invariant, so the solver sees the same problem whatever the
    forecast's arbitrary level.
  * per-leg breadth is a CONSTRAINT, exact and coefficient-free:
    ‖w_leg‖₂ ≤ (Σ|w_leg|)/√6  ⟺  any nonzero leg has N_eff ≥ 6, using the
    frozen 6 and nothing else. A market that cannot support six names a side
    yields the zero book, honestly.

DETERMINISM (§60.7): Clarabel interior-point, deterministic by construction;
assets must arrive lexicographically sorted (asserted — the caller cannot
introduce order-dependence); termination state OPTIMAL only, anything else is
an OperationalFailure (a D_operational day, never a silent fallback); post-
solve residual checks at the frozen maxima.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# §63.6: `cov` is either the diagonal rcm.factors.CovarianceModel (the
# K=0 independent-error geometry, with per-asset SE arrays) or the
# rcm.rescov.FullResidualCovarianceModel (Ω̂ with its own SE geometry,
# se arrays None) — detected by attributes, never imported here.

# frozen (§60.7 / §60.11.3)
ETA = 0.0010                 # 10 bps per side: 5 fee + 5 slippage (§56.2)
G_CAP = 3.0
NAME_CAP = G_CAP / 12        # 0.25 — hard safety ceiling ONLY (§60.11.3.2)
Z_CHANCE = 1.645
N_INTENDED = 10              # ε_β derivation input (§60.3.3)
SOLVER_TOL = 1e-8
DOLLAR_RESIDUAL_MAX = 1e-8 * G_CAP
FACTOR_RESIDUAL_SLACK = 1e-6
SHADOW_WEIGHT_TOL = 1e-5


class OperationalFailure(RuntimeError):
    """Solver did not terminate OPTIMAL, or a residual check failed.
    The day is D_operational; the failure is never silently absorbed."""


def epsilon_beta(sigma_target_daily: float, sigma_factor_daily: float) -> float:
    """§60.3.3: the factor may contribute at most one intended position's
    share of variance: ε = σ_target / (√N · σ_factor)."""
    if sigma_factor_daily <= 0:
        raise ValueError("factor vol must be positive")
    return sigma_target_daily / (np.sqrt(N_INTENDED) * sigma_factor_daily)


@dataclass(frozen=True)
class OptimizerResult:
    w: np.ndarray
    status: str
    objective: float
    gross: float
    dollar_residual: float
    beta_residual_btc: float
    beta_residual_eth: float
    vol_model: float


def _is_full_model(cov) -> bool:
    """§63.6.7.2: the full residual-covariance model is detected by its
    attributes — no import of rcm.rescov here (it imports this module)."""
    return getattr(cov, "omega_sqrt", None) is not None


def solve(symbols: list[str], mu: np.ndarray, cov,
          se_btc: np.ndarray | None, se_eth: np.ndarray | None,
          sigma_target_daily: float, w_prev: np.ndarray | None = None
          ) -> OptimizerResult:
    import cvxpy as cp

    n = len(symbols)
    if list(symbols) != sorted(symbols):
        raise ValueError("assets must be lexicographically sorted — solver "
                         "determinism depends on a canonical ordering (§60.7)")
    full = _is_full_model(cov)
    if full:
        # §63.6.4: the chance SE is [(XᵀX)⁻¹]_kk · wᵀΩ̂w — the model's own
        # geometry. Per-asset SE arrays belong to the independent-error
        # formula it replaces; passing both would be two risk models.
        if se_btc is not None or se_eth is not None:
            raise ValueError("the full residual-covariance model carries "
                             "its own SE geometry (§63.6.7.2); per-asset "
                             "SE arrays must be None")
        if len(mu) != n or cov.omega.shape != (n, n):
            raise ValueError("input lengths disagree")
    else:
        if se_btc is None or se_eth is None:
            raise ValueError("the diagonal model requires per-asset SE "
                             "arrays (the K=0 independent-error geometry)")
        if not (len(mu) == len(se_btc) == len(se_eth) == n):
            raise ValueError("input lengths disagree")
    w_prev = np.zeros(n) if w_prev is None else np.asarray(w_prev, float)

    eps_btc = epsilon_beta(sigma_target_daily, cov.sf_btc)
    eps_eth = epsilon_beta(sigma_target_daily, cov.sf_eth)

    # §62.8: center ONCE over the eligible set (the caller's input vector),
    # then partition by the centered sign. The mean is never recomputed.
    mu = np.asarray(mu, float)
    mu_c = mu - mu.mean()
    m_long = (mu_c > 0).astype(float)
    m_short = (mu_c < 0).astype(float)
    m_zero = (mu_c == 0).astype(float)

    w = cp.Variable(n)
    if full:
        resid_leg = cov.omega_sqrt @ w        # ‖Ω̂^{1/2}w‖₂² = wᵀΩ̂w
        chance_btc = Z_CHANCE * float(np.sqrt(cov.g_btc)) * cp.norm(resid_leg, 2)
        chance_eth = Z_CHANCE * float(np.sqrt(cov.g_eth)) * cp.norm(resid_leg, 2)
    else:
        resid_leg = cp.multiply(cov.d_idio, w)
        chance_btc = Z_CHANCE * cp.norm(cp.multiply(se_btc, w), 2)
        chance_eth = Z_CHANCE * cp.norm(cp.multiply(se_eth, w), 2)
    risk_vec = cp.hstack([cov.sf_btc * (cov.b_btc @ w),
                          cov.sf_eth * (cov.b_eth @ w),
                          resid_leg])
    constraints = [
        cp.sum(w) == 0,
        cp.norm(risk_vec, 2) <= sigma_target_daily,
        cp.abs(cov.b_btc @ w) + chance_btc <= eps_btc,
        cp.abs(cov.b_eth @ w) + chance_eth <= eps_eth,
        cp.norm(w, 1) <= G_CAP,
        cp.abs(w) <= NAME_CAP,
        # §62.2 sign pre-assignment: no name may be held against its signal
        cp.multiply(m_long, w) >= 0,
        cp.multiply(m_short, w) <= 0,
        cp.multiply(m_zero, w) == 0,
        # §62.2 exact per-leg breadth: nonzero leg ⇒ N_eff ≥ 6 (frozen)
        cp.norm(cp.multiply(m_long, w), 2)
        <= (m_long @ w) / np.sqrt(6.0),
        cp.norm(cp.multiply(m_short, w), 2)
        <= -(m_short @ w) / np.sqrt(6.0),
    ]
    prob = cp.Problem(cp.Maximize(mu_c @ w - ETA * cp.norm(w - w_prev, 1)),
                      constraints)
    prob.solve(solver=cp.CLARABEL)

    if prob.status != "optimal":
        raise OperationalFailure(
            f"solver terminated {prob.status!r}; only OPTIMAL is accepted "
            f"(§60.7). This day is D_operational.")

    wv = np.asarray(w.value, float)
    wv[np.abs(wv) < SOLVER_TOL] = 0.0          # deterministic zero-clean
    dollar = abs(float(np.sum(wv)))
    if full:
        resid_norm = float(np.linalg.norm(cov.omega_sqrt @ wv))
        rb = abs(float(cov.b_btc @ wv)) + Z_CHANCE * float(
            np.sqrt(cov.g_btc)) * resid_norm
        re = abs(float(cov.b_eth @ wv)) + Z_CHANCE * float(
            np.sqrt(cov.g_eth)) * resid_norm
    else:
        rb = abs(float(cov.b_btc @ wv)) + Z_CHANCE * float(
            np.linalg.norm(se_btc * wv))
        re = abs(float(cov.b_eth @ wv)) + Z_CHANCE * float(
            np.linalg.norm(se_eth * wv))
    if dollar > DOLLAR_RESIDUAL_MAX:
        raise OperationalFailure(
            f"dollar-neutrality residual {dollar:.2e} exceeds "
            f"{DOLLAR_RESIDUAL_MAX:.2e}")
    if rb > eps_btc + FACTOR_RESIDUAL_SLACK or re > eps_eth + FACTOR_RESIDUAL_SLACK:
        raise OperationalFailure(
            f"chance-constraint residual breach: BTC {rb:.3e}/{eps_btc:.3e}, "
            f"ETH {re:.3e}/{eps_eth:.3e}")
    return OptimizerResult(
        w=wv, status=prob.status, objective=float(prob.value),
        gross=float(np.sum(np.abs(wv))), dollar_residual=dollar,
        beta_residual_btc=rb, beta_residual_eth=re,
        vol_model=cov.portfolio_vol(wv),
    )


def degenerate_cause(symbols: list[str], mu: np.ndarray, cov,
                     se_btc: np.ndarray | None, se_eth: np.ndarray | None,
                     sigma_target_daily: float) -> str:
    """§62.8.4: WHY did the optimizer produce a zero book?

    A D_degenerate day must record its cause; "the model saw no opportunity"
    may not be claimed without this decomposition. The cascade uses only
    structure and the existing frozen tolerances — no new threshold:

      1. breadth: fewer than 6 names carry a nonzero centered forecast on a
         side, so the per-leg SOC zeroes that leg and neutrality zeroes the
         other — constraint_interaction, counted, not measured.
      2. chance: re-solve the identical instance WITHOUT the chance
         constraints (a diagnostic solve, synthetic-safe); if a nonzero book
         appears, the chance constraints were binding at zero.
      3. otherwise: no_trade — the centered forecast does not justify
         exposure against the frozen cost term.
    """
    mu_c = np.asarray(mu, float) - np.asarray(mu, float).mean()
    n_long = int(np.sum(mu_c > 0))
    n_short = int(np.sum(mu_c < 0))
    if n_long < 6 or n_short < 6:
        return (f"constraint_interaction:breadth "
                f"({n_long} long-side / {n_short} short-side candidates "
                f"vs the frozen 6)")
    import cvxpy as cp
    n = len(symbols)
    w = cp.Variable(n)
    m_long = (mu_c > 0).astype(float)
    m_short = (mu_c < 0).astype(float)
    m_zero = (mu_c == 0).astype(float)
    resid_leg = (cov.omega_sqrt @ w if _is_full_model(cov)
                 else cp.multiply(cov.d_idio, w))
    risk_vec = cp.hstack([cov.sf_btc * (cov.b_btc @ w),
                          cov.sf_eth * (cov.b_eth @ w),
                          resid_leg])
    cons = [cp.sum(w) == 0,
            cp.norm(risk_vec, 2) <= sigma_target_daily,
            cp.norm(w, 1) <= G_CAP, cp.abs(w) <= NAME_CAP,
            cp.multiply(m_long, w) >= 0, cp.multiply(m_short, w) <= 0,
            cp.multiply(m_zero, w) == 0,
            cp.norm(cp.multiply(m_long, w), 2) <= (m_long @ w) / np.sqrt(6.0),
            cp.norm(cp.multiply(m_short, w), 2) <= -(m_short @ w) / np.sqrt(6.0)]
    prob = cp.Problem(cp.Maximize(mu_c @ w - ETA * cp.norm(w, 1)), cons)
    prob.solve(solver=cp.CLARABEL)
    if (prob.status == "optimal" and w.value is not None
            and float(np.sum(np.abs(w.value))) > SOLVER_TOL * 100):
        return "constraint_interaction:chance"
    return "no_trade"
