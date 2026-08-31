"""
§60.7 + §60.11.3: the RCM optimizer — an SOCP with two knobs eliminated.

    max_w   μᵀw − η·‖w − w_prev‖₁
    s.t.    Σ w_i = 0                              exact dollar neutrality
            portfolio_vol(w) ≤ σ_daily             vol target as a CONSTRAINT
            |β̂_kᵀw| + z·‖SE_k ∘ w‖₂ ≤ ε_β,k       chance neutrality, k ∈ {BTC, ETH⊥}
            ‖w‖₁ ≤ G_cap                           the 3.0 backstop
            |w_i| ≤ G_cap / 12 = 0.25              hard safety cap (§60.11.3.2)

γ does not exist (vol is a constraint). η is not free — it IS the frozen
10 bps per-side cost stack. w = 0 is always feasible, so the problem is never
infeasible: a degenerate market produces a near-zero book that then fails the
GATES, which is where strategy semantics live.

DETERMINISM (§60.7): Clarabel interior-point, deterministic by construction;
assets must arrive lexicographically sorted (asserted — the caller cannot
introduce order-dependence); termination state OPTIMAL only, anything else is
an OperationalFailure (a D_operational day, never a silent fallback); post-
solve residual checks at the frozen maxima.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rcm.factors import CovarianceModel

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


def solve(symbols: list[str], mu: np.ndarray, cov: CovarianceModel,
          se_btc: np.ndarray, se_eth: np.ndarray,
          sigma_target_daily: float, w_prev: np.ndarray | None = None
          ) -> OptimizerResult:
    import cvxpy as cp

    n = len(symbols)
    if list(symbols) != sorted(symbols):
        raise ValueError("assets must be lexicographically sorted — solver "
                         "determinism depends on a canonical ordering (§60.7)")
    if not (len(mu) == len(se_btc) == len(se_eth) == n):
        raise ValueError("input lengths disagree")
    w_prev = np.zeros(n) if w_prev is None else np.asarray(w_prev, float)

    eps_btc = epsilon_beta(sigma_target_daily, cov.sf_btc)
    eps_eth = epsilon_beta(sigma_target_daily, cov.sf_eth)

    w = cp.Variable(n)
    risk_vec = cp.hstack([cov.sf_btc * (cov.b_btc @ w),
                          cov.sf_eth * (cov.b_eth @ w),
                          cp.multiply(cov.d_idio, w)])
    constraints = [
        cp.sum(w) == 0,
        cp.norm(risk_vec, 2) <= sigma_target_daily,
        cp.abs(cov.b_btc @ w) + Z_CHANCE * cp.norm(cp.multiply(se_btc, w), 2)
        <= eps_btc,
        cp.abs(cov.b_eth @ w) + Z_CHANCE * cp.norm(cp.multiply(se_eth, w), 2)
        <= eps_eth,
        cp.norm(w, 1) <= G_CAP,
        cp.abs(w) <= NAME_CAP,
    ]
    prob = cp.Problem(cp.Maximize(mu @ w - ETA * cp.norm(w - w_prev, 1)),
                      constraints)
    prob.solve(solver=cp.CLARABEL)

    if prob.status != "optimal":
        raise OperationalFailure(
            f"solver terminated {prob.status!r}; only OPTIMAL is accepted "
            f"(§60.7). This day is D_operational.")

    wv = np.asarray(w.value, float)
    wv[np.abs(wv) < SOLVER_TOL] = 0.0          # deterministic zero-clean
    dollar = abs(float(np.sum(wv)))
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
