"""
The 4 + 8 frozen feature families (NOTES 70.2.1). Exactly these
transforms; any change after the lock is a new specification.

All builders are pure array functions over a daily UTC date axis. Index
`t` is the TARGET day; every feature at row `t` uses only data knowable
at the 00:00:00 UTC boundary where day `t` begins (the caller supplies
series already on that axis; exogenous availability is enforced here via
explicit availability timestamps). NaN marks "not knowable yet".
"""

from __future__ import annotations

import numpy as np

VOL_WINDOW = 21                    # family 2, frozen
TREND_HORIZONS = (1, 5, 21)        # family 1, frozen
LAMBDA_DOC = "see g3.models.LAMBDA_GRID"

# Frozen feature orders (NOTES 70.6.4 / 70.6.5). Direction: M0 = 9,
# M1 = 19. Cross-section: M0 = 7, M1 = 13 — the exogenous state enters
# the cross-section ONLY through per-asset exposure interactions
# (beta_i,t x market move); a shared exogenous term is the recorded Q4
# degeneracy and is pinned impossible by test.
DIRECTION_M0 = ("trend_1d", "trend_5d", "trend_21d",
                "vol_21d", "volofvol_21d",
                "funding_level", "funding_mean",
                "xsec_dispersion", "breadth_positive")
CROSS_ASSET = ("sp500_ret_1d", "nasdaq100_ret_1d",
               "vix_level", "vix_chg_1d",
               "us2y_level", "us2y_chg_1d",
               "us10y_level", "us10y_chg_1d", "slope_2s10s",
               "usd_ret_1d")
DIRECTION_M1 = DIRECTION_M0 + CROSS_ASSET
XSEC_M0 = ("name_trend_1d", "name_trend_5d", "name_trend_21d",
           "name_vol_21d", "name_funding_level",
           "xsec_dispersion", "breadth_positive")
INTERACTIONS = ("name_int_spx", "name_int_ndx", "name_int_vix",
                "name_int_2y", "name_int_10y", "name_int_usd")
XSEC_M1 = XSEC_M0 + INTERACTIONS

BETA_WINDOW = 90                   # 70.6.6, trailing complete UTC days
BETA_MIN_OBS = 60                  # 70.6.6, else interactions MISSING


def trailing_returns(logret: np.ndarray,
                     horizons: tuple[int, ...] = TREND_HORIZONS
                     ) -> np.ndarray:
    """(n, len(horizons)); column h at row t = sum of logret over the h
    days ENDING at t-1 (knowable at the day-t boundary). NaN until the
    horizon is fully populated."""
    n = len(logret)
    out = np.full((n, len(horizons)), np.nan)
    for j, h in enumerate(horizons):
        for t in range(h, n):
            win = logret[t - h:t]
            if not np.isnan(win).any():
                out[t, j] = win.sum()
    return out


def realised_vol(logret: np.ndarray, window: int = VOL_WINDOW
                 ) -> np.ndarray:
    """(n,); row t = std (ddof=1) of the `window` daily log returns
    ending at t-1."""
    n = len(logret)
    out = np.full(n, np.nan)
    for t in range(window, n):
        win = logret[t - window:t]
        if not np.isnan(win).any():
            out[t] = win.std(ddof=1)
    return out


def vol_of_vol(logret: np.ndarray, window: int = VOL_WINDOW
               ) -> np.ndarray:
    """(n,); std (ddof=1) over the trailing `window` days of the daily
    realised-vol series (each day's vol from ITS trailing window)."""
    rv = realised_vol(logret, window)
    n = len(logret)
    out = np.full(n, np.nan)
    for t in range(window, n):
        win = rv[t - window:t]
        if not np.isnan(win).any():
            out[t] = win.std(ddof=1)
    return out


def exog_level_and_change(avail_ms: np.ndarray, values: np.ndarray,
                          boundary_ms: np.ndarray, log_change: bool
                          ) -> tuple[np.ndarray, np.ndarray]:
    """Level and 1-observation change of an exogenous series on the
    decision axis (NOTES 70.2.1): at each boundary instant, the level is
    the most recent observation with availability <= boundary, and the
    change is against the SECOND most recent — the change between the
    two most recent distinct PIT-available observations, never a
    zero-filled stale day. log_change: log ratio; else difference in
    level (yields, percentage points).

    avail_ms must be sorted ascending; observations after a boundary are
    invisible to it by construction.
    """
    order = np.argsort(avail_ms, kind="mergesort")
    a = np.asarray(avail_ms)[order]
    v = np.asarray(values, dtype=float)[order]
    n = len(boundary_ms)
    level = np.full(n, np.nan)
    change = np.full(n, np.nan)
    j = 0
    last = prev = np.nan
    for i in range(n):
        while j < len(a) and a[j] <= boundary_ms[i]:
            prev = last
            last = v[j]
            j += 1
        level[i] = last
        if not (np.isnan(last) or np.isnan(prev)):
            change[i] = (np.log(last / prev) if log_change
                         else last - prev)
    return level, change


def direction_matrix(feats: dict[str, np.ndarray], names: tuple[str, ...]
                     ) -> np.ndarray:
    """Stack per-day feature columns in the FROZEN order `names`."""
    missing = [k for k in names if k not in feats]
    if missing:
        raise KeyError(f"missing frozen features: {missing}")
    return np.column_stack([feats[k] for k in names])


def xsec_matrices(per_name: dict[str, dict[str, np.ndarray]],
                  common: dict[str, np.ndarray], names: tuple[str, ...]
                  ) -> dict[str, np.ndarray]:
    """Per asset: (n_days, len(names)) matrix in the frozen order.
    per_name[asset][feature] are per-name columns (name_* features);
    common[feature] are shared per-date columns."""
    out = {}
    for asset, fdict in per_name.items():
        cols = []
        for k in names:
            if k.startswith("name_"):
                cols.append(fdict[k])
            else:
                cols.append(common[k])
        out[asset] = np.column_stack(cols)
    return out


def beta_exposure(r_i: np.ndarray, r_x: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The frozen 70.6.6 exposure estimator on the daily axis: at row t,
    OLS slope (with intercept) of r_i on r_x over the trailing
    BETA_WINDOW days ENDING at t-1 (complete days only), requiring
    BETA_MIN_OBS valid paired days; otherwise NaN — the asset's
    interaction features are MISSING for that date, and the run stage
    excludes/counts/reports, never zero-fills. Returns (beta, se,
    n_valid); the standard error is recorded, never a feature."""
    n = len(r_i)
    beta = np.full(n, np.nan)
    se = np.full(n, np.nan)
    nv = np.zeros(n)
    for t in range(1, n):
        lo = max(0, t - BETA_WINDOW)
        yi = r_i[lo:t]
        xi = r_x[lo:t]
        m = np.isfinite(yi) & np.isfinite(xi)
        k = int(m.sum())
        nv[t] = k
        if t - lo < BETA_WINDOW or k < BETA_MIN_OBS:
            continue
        x, y = xi[m], yi[m]
        vx = ((x - x.mean()) ** 2).sum()
        if vx == 0:
            continue
        b = ((x - x.mean()) * (y - y.mean())).sum() / vx
        a = y.mean() - b * x.mean()
        resid = y - a - b * x
        beta[t] = b
        se[t] = np.sqrt((resid @ resid) / (k - 2) / vx)
    return beta, se, nv
