"""
§60.1: the two-factor model with ETH orthogonalized against BTC.

One estimation window (90 days) for everything in the system — betas, the
orthogonalization, factor variances, idio variances. No second window exists
to sweep.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FACTOR_WINDOW = 90          # §60.1: bound by window + 63 <= 180; frozen inside


class InsufficientHistory(ValueError):
    pass


def orthogonalize_eth(f_btc: np.ndarray, f_eth: np.ndarray) -> np.ndarray:
    """f_ETH⊥ = residual of ETH on BTC (with intercept) over the SAME window.

    In-window orthogonality is exact by construction; §60.1's V_i diagonality
    follows from it.
    """
    if len(f_btc) != len(f_eth):
        raise ValueError("factor series misaligned")
    x = f_btc - f_btc.mean()
    denom = float(x @ x)
    if denom <= 0:
        raise InsufficientHistory("degenerate BTC factor (zero variance)")
    slope = float(x @ (f_eth - f_eth.mean())) / denom
    resid = (f_eth - f_eth.mean()) - slope * x
    return resid


@dataclass(frozen=True)
class AssetBetas:
    beta_btc: float
    beta_eth_perp: float
    se_btc: float
    se_eth_perp: float
    sigma_eps: float          # daily idio stdev
    alpha: float


def estimate_betas(r_i: np.ndarray, f_btc: np.ndarray,
                   f_eth_perp: np.ndarray) -> AssetBetas:
    """Equal-weighted OLS with intercept (§60.1 frozen estimator).

    V_i is diagonal up to numerical error because the factors are orthogonal
    in-window; the diagonal entries are returned as per-factor SEs.
    """
    T = len(r_i)
    if not (len(f_btc) == len(f_eth_perp) == T):
        raise ValueError("series misaligned")
    if T < 30:
        raise InsufficientHistory(f"{T} < 30 observations")
    X = np.column_stack([np.ones(T), f_btc, f_eth_perp])
    coef, *_ = np.linalg.lstsq(X, r_i, rcond=None)
    resid = r_i - X @ coef
    dof = T - 3
    s2 = float(resid @ resid) / dof
    xtx_inv = np.linalg.inv(X.T @ X)
    ses = np.sqrt(np.maximum(s2 * np.diag(xtx_inv), 0.0))
    return AssetBetas(
        alpha=float(coef[0]), beta_btc=float(coef[1]),
        beta_eth_perp=float(coef[2]),
        se_btc=float(ses[1]), se_eth_perp=float(ses[2]),
        sigma_eps=float(np.sqrt(s2)),
    )


def residual_series(r_i: np.ndarray, f_btc: np.ndarray,
                    f_eth_perp: np.ndarray, b: AssetBetas) -> np.ndarray:
    """ε_i,t under the fitted betas — the momentum input (§60.2)."""
    return r_i - b.alpha - b.beta_btc * f_btc - b.beta_eth_perp * f_eth_perp


@dataclass(frozen=True)
class CovarianceModel:
    """Σ = B Σ_f Bᵀ + D from the single 90-day system (§60.7).

    D diagonal is an APPROXIMATION (§60.11.5.1): orthogonality to the factors
    is not cross-coin independence. The Stage-20 stress test probes exactly
    this, under a pre-registered fixture, and its pass is fenced to that
    fixture.
    """
    b_btc: np.ndarray          # (n,)
    b_eth: np.ndarray          # (n,)
    sf_btc: float              # daily factor stdevs
    sf_eth: float
    d_idio: np.ndarray         # (n,) daily idio stdevs

    def portfolio_vol(self, w: np.ndarray) -> float:
        v = ((self.sf_btc * float(self.b_btc @ w)) ** 2
             + (self.sf_eth * float(self.b_eth @ w)) ** 2
             + float(np.sum((self.d_idio * w) ** 2)))
        return float(np.sqrt(max(v, 0.0)))
