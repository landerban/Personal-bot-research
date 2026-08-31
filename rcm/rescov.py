"""
§63.6.4: the residual factor covariance estimator — RCM v1's residual-risk
model, replacing the diagonal approximation the §63.3/§63.5 measurement
contradicted.

    D_t    = diag(σ̂²_ε)                    the variances RCM already uses
    C_t    = eigendecomposition of the 90-day residual sample correlation
    λ_+    = (1 + √(N/87))²                 MP edge, raw eigenvalue units
    K_t    = #{ λ_j > λ_+ }                 preregistered rank rule; no fixed K
    L_t    = Σ_{j≤K} λ_j q_j q_jᵀ
    C_RCM  = L_t + diag(1 − diag(L_t))
    Ω̂_t    = D^{1/2} C_RCM D^{1/2}

Frozen properties (§63.6.4, proven in the ledger): marginals preserved
exactly; PSD; K = 0 reduces to the diagonal model automatically (no
fallback rule); RAW SPIKES — retained eigenvalues keep their observed
sample values, no shrinkage/de-biasing/clipping of any kind.

Floating-point policy (frozen; hygiene, not regularization): symmetrize
before eigh; an analytically-nonnegative quantity observed below
−SOLVER_TOL is a covariance INTEGRITY FAILURE (fail closed,
D_structural); within [−SOLVER_TOL, 0) it is zero-cleaned with the
maximum correction recorded.

Estimation, not selection (§60.0): the estimator runs on the PIT
risk-eligible set BEFORE any momentum score, expected-return sign,
weight, gate outcome, or performance conditioning — its inputs are a
correlation matrix and a variance vector, and nothing else exists in its
signature to condition on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rcm.gates import IntegrityFailure
from rcm.optimizer import SOLVER_TOL

M_DOF = 87                    # T = 90 minus the 3-regressor design, frozen


def mp_edge_raw(n: int) -> float:
    """λ_+ = (1 + √(n/87))² — raw eigenvalue units (≡ s_MP+ · n)."""
    if n < 1:
        raise ValueError("n must be positive")
    return float((1.0 + np.sqrt(n / M_DOF)) ** 2)


def apply_remainder_policy(r: np.ndarray, tol: float = SOLVER_TOL
                           ) -> tuple[np.ndarray, float]:
    """§63.6.4 frozen policy for an analytically-nonnegative vector:
    below −tol ⇒ integrity failure; in [−tol, 0) ⇒ exact zero, correction
    recorded. Returns (cleaned, max_correction)."""
    r = np.asarray(r, float)
    if not np.all(np.isfinite(r)):
        raise IntegrityFailure("non-finite remainder — covariance "
                               "singularity, fail closed (§63.6.4)")
    if np.any(r < -tol):
        raise IntegrityFailure(
            f"remainder {float(r.min()):.3e} < -{tol} — an analytically "
            f"nonnegative quantity is materially negative: covariance "
            f"integrity failure, fail closed, D_structural (§63.6.4)")
    neg = r < 0.0
    max_corr = float(-r[neg].min()) if np.any(neg) else 0.0
    return np.where(neg, 0.0, r), max_corr


def psd_sqrt(m: np.ndarray, tol: float = SOLVER_TOL) -> np.ndarray:
    """Symmetric PSD square root under the same frozen policy."""
    m = (np.asarray(m, float) + np.asarray(m, float).T) / 2.0
    lam, q = np.linalg.eigh(m)
    lam, _ = apply_remainder_policy(lam, tol)
    return (q * np.sqrt(lam)) @ q.T


def _fix_signs(q: np.ndarray) -> np.ndarray:
    """§63.6.7.4 determinism: each column's largest-|entry| component is
    made positive. qqᵀ terms are invariant; the stored vectors are not."""
    q = q.copy()
    for j in range(q.shape[1]):
        i = int(np.argmax(np.abs(q[:, j])))
        if q[i, j] < 0:
            q[:, j] = -q[:, j]
    return q


@dataclass(frozen=True)
class ResCov:
    c_rcm: np.ndarray             # the marginal-preserving correlation model
    omega: np.ndarray             # Ω̂ = D^{1/2} C_RCM D^{1/2}
    low_rank: np.ndarray          # L_t (zeros when K = 0)
    k: int                        # K_t by the MP-edge rule
    eigenvalues: np.ndarray       # full sample spectrum, descending
    retained_eigenvalues: np.ndarray   # the K raw spikes, sample values
    retained_vectors: np.ndarray  # sign-fixed, (n, K)
    mp_edge: float                # λ_+ in raw units
    n: int
    r_correction_max: float       # frozen zero-clean, recorded per date

    def report(self) -> dict:
        """§63.6.7.5: the day-level record — K_t and λ₁/tr with the §63.5
        null references beside them. The aggregate §59.11.4 tuple is
        untouched."""
        tr = float(self.eigenvalues.sum())
        return {"K_t": self.k,
                "lambda1_share": float(self.eigenvalues[0]) / tr,
                "mp_edge_raw": self.mp_edge,
                "mp_edge_share": self.mp_edge / self.n,
                "r_correction_max": self.r_correction_max}


def estimate(corr: np.ndarray, resid_var: np.ndarray) -> ResCov:
    """§63.6.4 verbatim. Inputs: the 90-day residual sample correlation on
    the PIT risk-eligible set, and the residual variances. NOTHING
    downstream (scores, weights, gates) exists in this signature."""
    c = np.asarray(corr, float)
    d = np.asarray(resid_var, float)
    n = c.shape[0]
    if c.shape != (n, n) or len(d) != n:
        raise ValueError("shape mismatch")
    if not np.all(np.isfinite(d)) or np.any(d <= 0.0):
        raise IntegrityFailure("residual variances must be finite and "
                               "positive — fail closed (§63.6.4)")
    c = (c + c.T) / 2.0                      # frozen symmetrization
    lam_asc, q_asc = np.linalg.eigh(c)
    lam = lam_asc[::-1]
    q = q_asc[:, ::-1]
    edge = mp_edge_raw(n)
    k = int(np.sum(lam > edge))

    if k == 0:
        # §63.6.4: C_RCM = I ⇒ Ω̂ = D — the automatic boundary case,
        # evaluated exactly (the same formula, not a fallback rule).
        return ResCov(c_rcm=np.eye(n), omega=np.diag(d),
                      low_rank=np.zeros((n, n)), k=0, eigenvalues=lam,
                      retained_eigenvalues=lam[:0],
                      retained_vectors=q[:, :0], mp_edge=edge, n=n,
                      r_correction_max=0.0)

    qk = _fix_signs(q[:, :k])
    lamk = lam[:k].copy()                    # RAW sample values, frozen
    low = (qk * lamk) @ qk.T
    r, max_corr = apply_remainder_policy(1.0 - np.diag(low))
    c_rcm = low + np.diag(r)
    d_half = np.sqrt(d)
    omega = c_rcm * np.outer(d_half, d_half)
    return ResCov(c_rcm=c_rcm, omega=omega, low_rank=low, k=k,
                  eigenvalues=lam, retained_eigenvalues=lamk,
                  retained_vectors=qk, mp_edge=edge, n=n,
                  r_correction_max=max_corr)


@dataclass(frozen=True)
class FullResidualCovarianceModel:
    """§63.6.7.2: Σ_model = BΣ_fBᵀ + Ω̂, one risk model for the optimizer,
    V_ret, and the chance constraint. g_btc/g_eth are [(XᵀX)⁻¹]_kk — one
    common design across assets, so they are model-level scalars. The
    optimizer detects this model by its attributes; per-asset SE arrays
    are FORBIDDEN with it (§63.6.4 replaces that geometry)."""
    b_btc: np.ndarray
    b_eth: np.ndarray
    sf_btc: float
    sf_eth: float
    omega: np.ndarray
    omega_sqrt: np.ndarray
    g_btc: float
    g_eth: float

    @classmethod
    def create(cls, b_btc, b_eth, sf_btc, sf_eth, omega, g_btc, g_eth
               ) -> "FullResidualCovarianceModel":
        omega = np.asarray(omega, float)
        return cls(b_btc=np.asarray(b_btc, float),
                   b_eth=np.asarray(b_eth, float),
                   sf_btc=float(sf_btc), sf_eth=float(sf_eth),
                   omega=omega, omega_sqrt=psd_sqrt(omega),
                   g_btc=float(g_btc), g_eth=float(g_eth))

    def portfolio_vol(self, w: np.ndarray) -> float:
        w = np.asarray(w, float)
        v = ((self.sf_btc * float(self.b_btc @ w)) ** 2
             + (self.sf_eth * float(self.b_eth @ w)) ** 2
             + float(w @ self.omega @ w))
        return float(np.sqrt(max(v, 0.0)))

    def se_chance(self, w: np.ndarray, which: str) -> float:
        """§63.6.4: SE_k(w) = √( [(XᵀX)⁻¹]_kk · wᵀΩ̂w )."""
        g = {"btc": self.g_btc, "eth": self.g_eth}[which]
        w = np.asarray(w, float)
        return float(np.sqrt(max(g * float(w @ self.omega @ w), 0.0)))
