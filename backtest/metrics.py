"""
Performance metrics, including the Deflated Sharpe Ratio.

Annualisation uses 365, not 252: perps trade every calendar day and the
equity curve has a point per calendar day. 252 would overstate Sharpe by
sqrt(365/252) ~ 1.20x.

Metrics are computed on the strategy window — from the first fill onward.
Including the warmup (flat equity while history accumulates) would dilute
both mean and vol with structural zeros.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

ANN = 365.0
_EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def strategy_window(result) -> tuple[list[int], np.ndarray]:
    """(timestamps, equity) from the first fill onward; empty if no trades."""
    if not result.rebalances:
        return [], np.array([])
    first_fill = result.rebalances[0].ts_fill
    idx = result.timestamps.index(first_fill)
    # Include the pre-fill equity point as the return base.
    lo = max(idx - 1, 0)
    return result.timestamps[lo:], np.array(result.equity[lo:])


def daily_returns(equity: np.ndarray) -> np.ndarray:
    if len(equity) < 2:
        return np.array([])
    prev = equity[:-1]
    if np.any(prev <= 0):
        # Equity through zero makes later returns meaningless; truncate at
        # the first non-positive point rather than emit garbage.
        cut = int(np.argmax(prev <= 0))
        equity = equity[: cut + 1]
        prev = equity[:-1]
        if len(equity) < 2:
            return np.array([])
    return np.diff(equity) / prev


def sharpe(returns: np.ndarray) -> float:
    """Annualised Sharpe (rf = 0). NaN when undefined."""
    if len(returns) < 2:
        return float("nan")
    sd = returns.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(returns.mean() / sd * math.sqrt(ANN))


def ann_vol(returns: np.ndarray) -> float:
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * math.sqrt(ANN))


def ann_return(equity: np.ndarray) -> float:
    """Geometric annualised return over the window."""
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0:
        return float("nan")
    years = (len(equity) - 1) / ANN
    return float((equity[-1] / equity[0]) ** (1 / years) - 1)


def max_drawdown(equity: np.ndarray) -> float:
    """Max peak-to-trough drawdown as a positive fraction."""
    if len(equity) < 2:
        return float("nan")
    peaks = np.maximum.accumulate(equity)
    dd = (peaks - equity) / peaks
    return float(dd.max())


def expected_max_dd(vol_ann: float, sharpe_ann: float) -> float:
    """
    Long-horizon E[MaxDD] for drifting Brownian motion: sigma^2/(2 mu)
    = sigma/(2 S). Only meaningful for positive Sharpe.
    """
    if not (sharpe_ann > 0) or not (vol_ann > 0):
        return float("nan")
    return vol_ann / (2.0 * sharpe_ann)


def active_days(returns: np.ndarray) -> int:
    """Days with a nonzero return, i.e. days a position was actually held."""
    return int((returns != 0).sum())


def hit_rate(returns: np.ndarray) -> float:
    """
    Fraction of ACTIVE days that were positive. Flat days (no position —
    warmup, universe gaps) are excluded: counting them as losses made a
    thinly-traded window read as a 7% hit rate. Sharpe is deliberately not
    given the same treatment — flat days understate it, the safe direction.
    """
    nz = returns[returns != 0]
    if len(nz) == 0:
        return float("nan")
    return float((nz > 0).mean())


def avg_win_loss(returns: np.ndarray) -> tuple[float, float]:
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    aw = float(wins.mean()) if len(wins) else float("nan")
    al = float(losses.mean()) if len(losses) else float("nan")
    return aw, al


def turnover_annualised(
    total_turnover: float, equity: np.ndarray
) -> float:
    """Annualised turnover as a multiple of average capital."""
    if len(equity) < 2:
        return float("nan")
    years = (len(equity) - 1) / ANN
    mean_eq = float(equity.mean())
    if years <= 0 or mean_eq <= 0:
        return float("nan")
    return total_turnover / years / mean_eq


def fee_drag(total_fees: float, gross_pnl: float) -> float:
    """
    Fees as a fraction of gross (pre-cost, price-only) PnL — the headline
    number. NaN when gross PnL is not positive: a drag ratio against a loss
    is not meaningful, and reporting one would hide the loss.
    """
    if gross_pnl <= 0:
        return float("nan")
    return total_fees / gross_pnl


def deflated_sharpe(
    observed_sr_daily: float,
    n_obs: int,
    trial_srs_daily: list[float],
    skew: float,
    kurt: float,
) -> float:
    """
    Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio.

    Probability that the observed (daily-frequency) Sharpe exceeds the
    expected maximum Sharpe from `len(trial_srs_daily)` trials under the
    null. `kurt` is Pearson kurtosis (normal = 3). NaN with fewer than two
    trials — the deflation benchmark needs a cross-trial variance.
    """
    n_trials = len(trial_srs_daily)
    if n_trials < 2 or n_obs < 2:
        return float("nan")
    var_sr = float(np.var(np.array(trial_srs_daily), ddof=1))
    if var_sr <= 0:
        return float("nan")
    sr_max = math.sqrt(var_sr) * (
        (1 - _EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
        + _EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )
    denom = 1 - skew * observed_sr_daily + (kurt - 1) / 4 * observed_sr_daily**2
    if denom <= 0:
        return float("nan")
    z = (observed_sr_daily - sr_max) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return float(_N.cdf(z))


def moments(returns: np.ndarray) -> tuple[float, float]:
    """(skew, Pearson kurtosis) of daily returns; (0, 3) fallback if tiny."""
    if len(returns) < 4:
        return 0.0, 3.0
    r = returns - returns.mean()
    sd = returns.std(ddof=0)
    if sd == 0:
        return 0.0, 3.0
    skew = float((r**3).mean() / sd**3)
    kurt = float((r**4).mean() / sd**4)
    return skew, kurt


def sharpe_bootstrap_ci(
    returns: np.ndarray,
    confidence: float = 0.90,
    n_boot: int = 2000,
    seed: int = 0,
    mean_block: float | None = None,
) -> tuple[float, float]:
    """
    Stationary-bootstrap confidence interval for the annualised Sharpe ratio
    (Politis-Romano 1994): resample geometric-length blocks with wraparound,
    so serial dependence survives resampling.

    Stage 2e 9: crypto returns are neither Gaussian nor IID, so the
    parametric SE understates uncertainty. Resampling an existing result is
    not a new backtest and costs no trial.

    Default mean block length is n**(1/3), the usual rule of thumb.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return (float("nan"), float("nan"))
    if mean_block is None:
        mean_block = max(2.0, n ** (1.0 / 3.0))
    p = 1.0 / mean_block
    rng = np.random.default_rng(seed)
    # index walk: with prob p start a new block at a random point, else step on
    starts = rng.integers(0, n, size=(n_boot, n))
    jumps = rng.random((n_boot, n)) < p
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jumps[:, j], starts[:, j], (idx[:, j - 1] + 1) % n)
    samples = r[idx]
    mu = samples.mean(axis=1)
    sd = samples.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = np.where(sd > 0, mu / sd * math.sqrt(ANN), np.nan)
    sr = sr[np.isfinite(sr)]
    if len(sr) < n_boot // 10:
        return (float("nan"), float("nan"))
    lo = (1.0 - confidence) / 2.0 * 100.0
    return (float(np.percentile(sr, lo)), float(np.percentile(sr, 100.0 - lo)))
