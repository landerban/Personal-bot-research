"""
§60.5 + §60.11.4–8 + §63.1: the feasibility gates.

UNRESOLVED MEANS RAISE (Stage 20 ground rule 2) — and RESOLVED means the
raise is removed by the ledger entry that resolved it, never by convenience:

  * `g_min` — WITHDRAWN in §60.11.6, RESOLVED in §63.1.A.2: replaced by the
    exposure-retention gate `V_ret >= V_RET_MIN`, the risk owner's preference
    stated before any Gen-2 return exists. Evaluated under the OPTIMIZER'S
    covariance model (§63.1.A.2.1: one risk model, two uses — never a
    second estimator). `G_realized / G_pre` remains DIAGNOSTIC ONLY.
  * zero momentum mass — RESOLVED in §63.1.A.1, user decision (a): coverage
    is the distinct value "N/A" (not 0, 1, or NaN — §63.1.5.1), the book may
    form, and the day carries the literal CARRY REGIME label (momentum.py).

§63.1.A.2.2: the absolute ceiling still binds. V_ret is a lower bound only;
the executable book must also satisfy w_realᵀΣw_real <= σ²_target — enforced
here because quantization and composition changes downstream of the
optimizer can REGAIN risk (dropping a hedging short raises modeled
variance). Existing invariant, not a new tolerance.

§63.1.5.3: a nonzero w_pre whose modeled variance is zero/non-finite is a
covariance/model INTEGRITY failure — fail closed, D_structural, alert — not
an economic zero. The near-zero criterion is scale-free and reuses the
frozen §60.7 solver precision: modeled vol < SOLVER_TOL · G_pre.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rcm.factors import CovarianceModel
from rcm.optimizer import SOLVER_TOL

N_EFF_MIN = 6.0              # per leg, §60.5 (architecture-derived, retained)
# Numerical hygiene, NOT a threshold change: the §62.2 construction makes
# the per-leg SOC bind at exactly 6, and interior-point precision leaves
# the realized value ~4e-8 under. The frozen 6 is compared with the same
# margin logic §60.7 used for the shadow tolerance: 100x above solver
# precision (1e-8), far below anything economically meaningful.
N_EFF_NUMERICAL_TOL = 1e-6

# §63.1.A.2 — THE USER'S PREFERENCE, permanently fixed; the single
# definition in rcm/ (grep-tested). The covariance it is evaluated under is
# whatever the frozen specification adopts (§63.1.A.2.1), never a second
# model.
V_RET_MIN = 0.40

# §63.1.5.1: the distinct N/A value for signal coverage on zero-momentum-
# mass days. A string on purpose: arithmetic on it fails loudly instead of
# silently averaging into a rate.
COVERAGE_NA = "N/A"


class Unresolved(RuntimeError):
    """A quantity the ledger marks UNRESOLVED was needed. The answer is a
    ledger entry, never a default in code. (No gate currently raises this —
    §63.1 resolved both former cases — but the rule and the mechanism
    stand for whatever the ledger marks UNRESOLVED next.)"""


class DegenerateTarget(RuntimeError):
    """G_pre = 0 (§60.11.8.1): a named deterministic state, not a NaN."""


class IntegrityFailure(RuntimeError):
    """§63.1.5.3: nonzero w_pre with zero/non-finite modeled variance — a
    singularity in the covariance model, never an economic zero. The caller
    classifies the day D_structural and raises an alert; nothing downstream
    of this exception may execute."""


def n_eff(weights: np.ndarray) -> float:
    """Herfindahl-equivalent count. Bounds NO individual weight (§60.11.4):
    N_eff >= 6 and |w_i| <= 0.25 are complementary, non-equivalent controls."""
    w = np.abs(np.asarray(weights, float))
    s = float(w.sum())
    if s <= 0:
        return 0.0
    return s * s / float(np.sum(w * w))


def g_pre(w_pre: np.ndarray) -> float:
    """The gate denominator, and nothing else (§60.11.3.1)."""
    return float(np.sum(np.abs(w_pre)))


def c_signal(w_pre: np.ndarray, mu_momentum: np.ndarray,
             survive_mask: np.ndarray):
    """§60.5.1 bounded coverage with S_i = |μ_mom,i| (§60.11.7, delegate-
    adopted): which part of the HYPOTHESIS survived feasibility.

    Zero denominator = zero momentum mass = §63.1.A.1, USER DECISION (a):
    coverage is N/A — a distinct value, not a gate failure, not 0/1/NaN.
    The day's book may form and carries the CARRY REGIME label.
    """
    w = np.abs(np.asarray(w_pre, float))
    s = np.abs(np.asarray(mu_momentum, float))
    denom = float(np.sum(w * s))
    if denom == 0.0:
        return COVERAGE_NA
    kept = float(np.sum(w[survive_mask] * s[survive_mask]))
    return kept / denom          # in [0, 1] by construction


@dataclass(frozen=True)
class GateConfig:
    """v_ret_min is §63.1.A.2's frozen value — the user's recorded
    preference, not a tunable; defined ONCE as V_RET_MIN (grep-tested).
    c_signal_min is §60.5's majority-identity rule, unwithdrawn."""
    v_ret_min: float = V_RET_MIN
    n_eff_min: float = N_EFF_MIN
    c_signal_min: float = 0.50


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    failed_gates: tuple[str, ...]
    n_eff_long: float
    n_eff_short: float
    v_ret: float                 # §63.1.A.2: the exposure-retention ratio
    g_ratio: float               # DIAGNOSTIC ONLY (§63.1.A.2) — never gated
    coverage: object             # float in [0,1], or COVERAGE_NA (§63.1.A.1)


def evaluate(w_pre: np.ndarray, w_real: np.ndarray,
             mu_momentum: np.ndarray, cfg: GateConfig,
             cov: CovarianceModel, sigma_target_daily: float) -> GateVerdict:
    """All gates on the sized book, against the canonical pre-feasibility
    book, under THE OPTIMIZER'S covariance object (§63.1.A.2.1 — the caller
    passes the same `cov` it solved with; no second estimator exists).

    Raises DegenerateTarget on G_pre = 0 and IntegrityFailure on a nonzero
    book with zero/non-finite modeled variance (§63.1.5.3, fail closed).
    """
    gp = g_pre(w_pre)
    if gp == 0.0:
        raise DegenerateTarget("G_pre = 0 — degenerate_target (§60.11.8.1)")

    w_pre = np.asarray(w_pre, float)
    w_real = np.asarray(w_real, float)
    vol_pre = float(cov.portfolio_vol(w_pre))
    vol_real = float(cov.portfolio_vol(w_real))
    # §63.1.5.3: scale-free integrity check under the frozen solver
    # precision. A throttled micro-book has per-unit vol ~ idio vol and
    # passes; a nonzero book in a modeled nullspace cannot.
    if not np.isfinite(vol_pre) or vol_pre < SOLVER_TOL * gp:
        raise IntegrityFailure(
            f"nonzero w_pre (G_pre={gp:.3e}) with modeled vol "
            f"{vol_pre!r} < {SOLVER_TOL} * G_pre — covariance/model "
            f"singularity. Fail closed: D_structural, alert (§63.1.5.3).")
    v_ret = (vol_real ** 2) / (vol_pre ** 2)

    longs = np.where(w_real > 0, w_real, 0.0)
    shorts = np.where(w_real < 0, -w_real, 0.0)
    nl, ns = n_eff(longs), n_eff(shorts)
    g_ratio = float(np.sum(np.abs(w_real))) / gp
    survive = np.abs(w_real) > 0
    coverage = c_signal(w_pre, mu_momentum, survive)

    failed = []
    if nl < cfg.n_eff_min - N_EFF_NUMERICAL_TOL:
        failed.append("n_eff_long")
    if ns < cfg.n_eff_min - N_EFF_NUMERICAL_TOL:
        failed.append("n_eff_short")
    if v_ret < cfg.v_ret_min:
        failed.append("exposure_retention")
    # §63.1.A.2.2 companion invariant: the frozen absolute ceiling, with the
    # frozen 100x-above-solver-precision relative margin (§63.1.5.4).
    if vol_real ** 2 > sigma_target_daily ** 2 * (1 + N_EFF_NUMERICAL_TOL):
        failed.append("vol_ceiling")
    # coverage N/A is NOT a gate failure (§63.1.A.1) — the comparison only
    # exists when the momentum hypothesis has mass.
    if coverage is not COVERAGE_NA and coverage < cfg.c_signal_min:
        failed.append("signal_coverage")
    return GateVerdict(passed=not failed, failed_gates=tuple(failed),
                       n_eff_long=nl, n_eff_short=ns, v_ret=v_ret,
                       g_ratio=g_ratio, coverage=coverage)
