"""
The Q1-Q4 evaluators (NOTES 70.3), built ON the inherited machinery.

The interval construction is `rcm.eval_ic.stationary_bootstrap_ci`
UNCHANGED (Gen-1's construction per NOTES 60.12.3). Paired statistics
hand it the day-index series 0..n-1 and a stat_fn that maps each
resampled index row to the statistic on the PAIRED per-day components
(70.3.2) — one index walk per call, identical walks across a criterion
family because the seed, n and n_boot are identical. A test proves the
index-series call reproduces the direct call bit-for-bit.

Verdicts: PASS iff CI_lower > 0; INDETERMINATE iff the interval is
undefined under the inherited guards (n < 30 days, or < n_boot/10
finite replicates); FAIL otherwise. Conjunctions per 68.11.4.1 /
70.3.2: any FAIL leg -> FAIL; else any INDETERMINATE leg ->
INDETERMINATE; else PASS.
"""

from __future__ import annotations

import math

import numpy as np

from rcm.eval_ic import seed_from_lock_commit, stationary_bootstrap_ci

__all__ = ["seed_from_lock_commit", "brier_series", "bss",
           "ci_paired", "verdict_from_ci", "conjunctive",
           "q1_direction", "q2_incremental_direction",
           "q3_cross_sectional", "q4_incremental_cross_sectional"]


# ------------------------------------------------------------- Brier

def brier_series(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-day squared probability error."""
    return (np.asarray(p, float) - np.asarray(y, float)) ** 2


def bss(bs_model: np.ndarray, bs_clim: np.ndarray) -> float:
    """Pooled Brier skill score over the given days (70.3.1)."""
    denom = float(np.sum(bs_clim))
    if denom == 0:
        return float("nan")
    return 1.0 - float(np.sum(bs_model)) / denom


# ------------------------------------------------- paired bootstrap

def ci_paired(n_days: int, stat_from_idx, seed: int
              ) -> tuple[float, float]:
    """The inherited walker on the day-index series; stat_from_idx maps
    an (n_boot, n) integer index matrix to per-replicate statistics."""
    idx_series = np.arange(n_days, dtype=float)

    def stat_fn(samples: np.ndarray) -> np.ndarray:
        return stat_from_idx(samples.astype(np.int64))

    return stationary_bootstrap_ci(idx_series, stat_fn, seed=seed)


def verdict_from_ci(ci: tuple[float, float]) -> str:
    lo = ci[0]
    if lo is None or math.isnan(lo):
        return "INDETERMINATE"
    return "PASS" if lo > 0 else "FAIL"


def conjunctive(*verdicts: str) -> str:
    if any(v == "FAIL" for v in verdicts):
        return "FAIL"
    if any(v == "INDETERMINATE" for v in verdicts):
        return "INDETERMINATE"
    return "PASS"


def _ci_dict(stat: float, ci: tuple[float, float]) -> dict:
    lo, hi = ci
    half = (hi - lo) / 2.0 if np.isfinite(lo) and np.isfinite(hi) \
        else float("nan")
    return {"stat": stat, "ci90": (lo, hi),
            "ci_half_width": half,        # 70.3.3 resolving precision
            "verdict": verdict_from_ci(ci)}


# ---------------------------------------------------------- Q1 / Q2

def q1_direction(bs_m0: np.ndarray, bs_clim: np.ndarray, seed: int
                 ) -> dict:
    """Q1: CI_lower(BSS_M0) > 0."""
    n = len(bs_m0)
    ci = ci_paired(n, lambda I: 1.0 - bs_m0[I].sum(axis=1)
                   / bs_clim[I].sum(axis=1), seed)
    return _ci_dict(bss(bs_m0, bs_clim), ci)


def q2_incremental_direction(bs_m0: np.ndarray, bs_m1: np.ndarray,
                             bs_clim: np.ndarray, seed: int) -> dict:
    """Q2 conjunctive: CI_lower(BSS_M1) > 0 AND
    CI_lower(BSS_M1 - BSS_M0) > 0, paired on one identical walk per leg
    (same seed, same n)."""
    n = len(bs_m0)
    ci_m1 = ci_paired(n, lambda I: 1.0 - bs_m1[I].sum(axis=1)
                      / bs_clim[I].sum(axis=1), seed)
    ci_diff = ci_paired(
        n, lambda I: (bs_m0[I].sum(axis=1) - bs_m1[I].sum(axis=1))
        / bs_clim[I].sum(axis=1), seed)
    leg1, leg2 = _ci_dict(bss(bs_m1, bs_clim), ci_m1), \
        _ci_dict(bss(bs_m1, bs_clim) - bss(bs_m0, bs_clim), ci_diff)
    return {"leg_bss_m1": leg1, "leg_bss_diff": leg2,
            "verdict": conjunctive(leg1["verdict"], leg2["verdict"])}


# ---------------------------------------------------------- Q3 / Q4

def q3_cross_sectional(ic_m0: np.ndarray, seed: int) -> dict:
    """Q3: CI_lower(mean IC_M0) > 0 over M0's defined dates — the
    60.12.3 estimand, same machinery."""
    s = np.asarray(ic_m0, float)
    s = s[np.isfinite(s)]
    ci = stationary_bootstrap_ci(s, lambda m: m.mean(axis=1), seed=seed)
    return _ci_dict(float(s.mean()) if len(s) else float("nan"), ci)


def q4_incremental_cross_sectional(ic_m0: np.ndarray, ic_m1: np.ndarray,
                                   seed: int) -> dict:
    """Q4 conjunctive: CI_lower(mean IC_M1) > 0 on M1's defined dates
    AND CI_lower(mean(IC_M1 - IC_M0)) > 0 paired on the common defined
    dates (70.3.2)."""
    m1 = np.asarray(ic_m1, float)
    m1d = m1[np.isfinite(m1)]
    ci_m1 = stationary_bootstrap_ci(m1d, lambda m: m.mean(axis=1),
                                    seed=seed)
    both = np.isfinite(ic_m0) & np.isfinite(ic_m1)
    diff = (np.asarray(ic_m1, float) - np.asarray(ic_m0, float))[both]
    ci_diff = stationary_bootstrap_ci(diff, lambda m: m.mean(axis=1),
                                      seed=seed)
    leg1 = _ci_dict(float(m1d.mean()) if len(m1d) else float("nan"), ci_m1)
    leg2 = _ci_dict(float(diff.mean()) if len(diff) else float("nan"),
                    ci_diff)
    return {"leg_ic_m1": leg1, "leg_ic_diff": leg2,
            "n_common_dates": int(both.sum()),
            "verdict": conjunctive(leg1["verdict"], leg2["verdict"])}
