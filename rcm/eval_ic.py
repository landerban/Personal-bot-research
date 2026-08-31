"""
§60.12.3 (Stage 23b): the residual-momentum IC kill-criterion evaluator —
criterion 2 of §60.8, completed by the user's recorded decisions.

  IC_t = Spearman_i( Z_mom,i,t , ε_fwd,i,t )   average ranks for ties
  IC̄   = equal-weighted mean over defined dates (every date weight 1,
         irrespective of cross-section size — the USER-approved estimand)
  PASS iff CI_lower(IC̄) > 0 under the inherited stationary bootstrap

The evaluation cross-section is the frozen pre-alpha PIT risk-eligible
universe with complete ε_fwd — NOT conditioned on capability, formation,
weights, sign partition, gates, or PnL (§60.12.3): a 10-name date is
included. Undefined dates (fewer than two valid pairs, or a constant rank
vector) are EXCLUDED from the mean and bootstrap, counted, and reported
with their reason — never silently zero.

THE INTERVAL CONSTRUCTION IS GEN-1'S CODE, INHERITED LINE-FOR-LINE
(§60.12.3): `backtest/metrics.py :: sharpe_bootstrap_ci`, sha256
061622ed3e786d6dd6e91e5a16c65a4e82634486414d3fc065c0c3f312551328 at
citation — Politis–Romano stationary bootstrap, vectorized geometric-
block index walk with wraparound, percentile interval, the n < 30 NaN
guard and the < n_boot/10 finite-replicate guard. `stationary_bootstrap_ci`
below generalizes ONLY the statistic (a stat_fn over the resampled
matrix); a test proves bit-exact equivalence against the Gen-1 function.

Seed: derived deterministically from the §64 lock-commit hash
(`seed_from_lock_commit`) — new engineering governance, not precedent.

This module reads no return and imports no data reader.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

import numpy as np

N_BOOT = 2000                  # Gen-1 precedent, confirmed (§60.12.3)
CONFIDENCE = 0.90              # two-sided; CI_lower > 0 == one-sided 5%


def seed_from_lock_commit(commit_hex: str) -> int:
    """§60.12.3: seed = int(sha256(lock_commit_hex)[:8], 16)."""
    return int(hashlib.sha256(commit_hex.encode()).hexdigest()[:8], 16)


def average_ranks(v: np.ndarray) -> np.ndarray:
    """Average ranks (§60.12.3: ties -> average), 1-based."""
    v = np.asarray(v, float)
    order = np.argsort(v, kind="mergesort")
    sv = v[order]
    ranks = np.empty(len(v))
    base = np.arange(1, len(v) + 1, dtype=float)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = base[i:j + 1].mean()
        i = j + 1
    return ranks


def spearman_ic(z_mom: np.ndarray, eps_fwd: np.ndarray
                ) -> tuple[float | None, str | None]:
    """One date's IC, or (None, reason) when mathematically undefined."""
    z = np.asarray(z_mom, float)
    e = np.asarray(eps_fwd, float)
    if len(z) != len(e):
        raise ValueError("cross-section misaligned")
    ok = np.isfinite(z) & np.isfinite(e)
    z, e = z[ok], e[ok]
    if len(z) < 2:
        return None, "fewer_than_2_pairs"
    rz, re_ = average_ranks(z), average_ranks(e)
    sz, se_ = rz.std(ddof=1), re_.std(ddof=1)
    if sz == 0.0 or se_ == 0.0:
        return None, "constant_ranks"
    ic = float(np.corrcoef(rz, re_)[0, 1])
    return ic, None


def stationary_bootstrap_ci(series: np.ndarray, stat_fn,
                            confidence: float = CONFIDENCE,
                            n_boot: int = N_BOOT, seed: int = 0,
                            mean_block: float | None = None
                            ) -> tuple[float, float]:
    """Gen-1's construction, line-for-line; only the statistic differs.
    stat_fn maps the (n_boot, n) resampled matrix to n_boot statistics."""
    r = np.asarray(series, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return (float("nan"), float("nan"))
    if mean_block is None:
        mean_block = max(2.0, n ** (1.0 / 3.0))
    p = 1.0 / mean_block
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(n_boot, n))
    jumps = rng.random((n_boot, n)) < p
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jumps[:, j], starts[:, j], (idx[:, j - 1] + 1) % n)
    samples = r[idx]
    stats = np.asarray(stat_fn(samples), dtype=float)
    stats = stats[np.isfinite(stats)]
    if len(stats) < n_boot // 10:
        return (float("nan"), float("nan"))
    lo = (1.0 - confidence) / 2.0 * 100.0
    return (float(np.percentile(stats, lo)),
            float(np.percentile(stats, 100.0 - lo)))


@dataclass(frozen=True)
class ICVerdict:
    passed: bool
    ic_mean: float
    ci90: tuple[float, float]
    n_defined: int
    n_excluded: int
    exclusion_reasons: dict
    mean_block: float
    n_boot: int
    seed: int
    daily_ic: tuple             # chronological, defined dates only


def evaluate(records: list[dict], seed: int) -> ICVerdict:
    """records: [{date_ms, z_mom, eps_fwd}, ...] — the frozen evaluation
    cross-section per date, in any order; evaluated chronologically."""
    ics, reasons = [], Counter()
    for rec in sorted(records, key=lambda r: r["date_ms"]):
        ic, reason = spearman_ic(rec["z_mom"], rec["eps_fwd"])
        if ic is None:
            reasons[reason] += 1
        else:
            ics.append(ic)
    series = np.asarray(ics, float)
    n = len(series)
    mean_block = max(2.0, n ** (1.0 / 3.0)) if n else 2.0
    ci = stationary_bootstrap_ci(series, lambda s: s.mean(axis=1), seed=seed)
    ic_mean = float(series.mean()) if n else float("nan")
    passed = bool(np.isfinite(ci[0]) and ci[0] > 0.0)
    return ICVerdict(passed=passed, ic_mean=ic_mean, ci90=ci, n_defined=n,
                     n_excluded=int(sum(reasons.values())),
                     exclusion_reasons=dict(reasons), mean_block=mean_block,
                     n_boot=N_BOOT, seed=seed, daily_ic=tuple(ics))
