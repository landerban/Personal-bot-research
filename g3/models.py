"""
Frozen forecast forms (NOTES 70.6.7): L2 logistic regression (direction)
and L2 linear/ridge regression (cross-section), intercepts unpenalised,
deterministic solvers, explicit grids, calendar-boundary expanding inner
folds (built by g3.sequential.inner_folds), strongest-regularisation
tie-break. Nothing here is ever selected against out-of-sample-in-time
results.
"""

from __future__ import annotations

import numpy as np

# 70.6.7 frozen grids. Logistic penalty is (1/(2C))*||w_slopes||^2, so
# SMALLER C = stronger; ridge penalty is alpha*||w_slopes||^2, so LARGER
# alpha = stronger. Ties break toward the STRONGEST regularisation.
C_GRID = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
ALPHA_GRID = (0.001, 0.01, 0.1, 1.0, 10.0)
MAX_ITER = 200
TOL = 1e-10


def standardize_fit(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Training-window mean/std (ddof=1). Constant feature -> std 1 so
    the z-score is exactly zero (the 70.2.1 guard)."""
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=1)
    sd = np.where(sd == 0, 1.0, sd)
    return mu, sd


def standardize_apply(X: np.ndarray, mu: np.ndarray, sd: np.ndarray
                      ) -> np.ndarray:
    return (X - mu) / sd


def logistic_fit(X: np.ndarray, y: np.ndarray, C: float) -> np.ndarray:
    """Newton/IRLS for the 70.6.7 objective sum(nll) +
    (1/(2C))*||w_slopes||^2; intercept (column of ones prepended
    internally) unpenalised. C = 0 disables the penalty (the Platt
    two-parameter map). Returns w of shape (p+1,)."""
    n, p = X.shape
    A = np.column_stack([np.ones(n), X])
    w = np.zeros(p + 1)
    pen = np.full(p + 1, 1.0 / C if C > 0 else 0.0)
    pen[0] = 0.0
    for _ in range(MAX_ITER):
        z = A @ w
        prob = 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))
        g = A.T @ (prob - y) + pen * w
        s = np.clip(prob * (1 - prob), 1e-12, None)
        H = (A * s[:, None]).T @ A + np.diag(pen)
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < TOL:
            break
    return w


def logistic_predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    z = np.column_stack([np.ones(len(X)), X]) @ w
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def ridge_fit(X: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    """Closed-form for the 70.6.7 objective ||y - Aw||^2 +
    alpha*||w_slopes||^2; intercept unpenalised. Returns w of shape
    (p+1,) with w[0] the intercept."""
    n, p = X.shape
    A = np.column_stack([np.ones(n), X])
    pen = np.full(p + 1, float(alpha))
    pen[0] = 0.0
    return np.linalg.solve(A.T @ A + np.diag(pen), A.T @ y)


def ridge_predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X]) @ w


def mse(pred: np.ndarray, y: np.ndarray) -> float:
    return float(((pred - y) ** 2).mean())


def select_penalty(X: np.ndarray, y: np.ndarray, kind: str,
                   folds: list[tuple[list[int], list[int]]]
                   ) -> tuple[float, dict[float, float]]:
    """The FROZEN 70.6.7 selection: expanding calendar folds (built by
    g3.sequential.inner_folds), mean inner-fold log-loss (logistic) /
    MSE (ridge), ties broken toward the STRONGEST regularisation
    (smallest C / largest alpha). Standardisation is fit on each fold's
    fit rows only. Returns (chosen, per-value mean losses)."""
    grid = C_GRID if kind == "logistic" else ALPHA_GRID
    losses: dict[float, float] = {}
    for val in grid:
        fold_losses = []
        for fit_idx, val_idx in folds:
            mu, sd = standardize_fit(X[fit_idx])
            Xf = standardize_apply(X[fit_idx], mu, sd)
            Xv = standardize_apply(X[val_idx], mu, sd)
            if kind == "logistic":
                w = logistic_fit(Xf, y[fit_idx], val)
                fold_losses.append(
                    log_loss(logistic_predict(w, Xv), y[val_idx]))
            elif kind == "ridge":
                w = ridge_fit(Xf, y[fit_idx], val)
                fold_losses.append(mse(ridge_predict(w, Xv), y[val_idx]))
            else:
                raise ValueError(kind)
        losses[val] = float(np.mean(fold_losses))
    # strongest-regularisation tie-break: iterate weakest -> strongest;
    # <= lets the stronger value win exact ties.
    order = sorted(grid, reverse=(kind == "logistic"))
    best = None
    for v in order:
        if best is None or losses[v] <= losses[best]:
            best = v
    return best, losses
