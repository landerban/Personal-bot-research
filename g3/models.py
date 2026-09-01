"""
Frozen forecast forms (NOTES 70.2.2): L2 logistic regression (direction)
and ridge regression (cross-section), intercepts unpenalised,
deterministic solvers, and the frozen nested expanding-window selection.
Nothing here is ever selected against out-of-sample-in-time results.
"""

from __future__ import annotations

import numpy as np

LAMBDA_GRID = (1e-3, 1e-2, 1e-1, 1.0, 1e1, 1e2, 1e3)   # frozen, both models
INNER_FOLDS = ((0.40, 0.15), (0.55, 0.15), (0.70, 0.15))  # frozen (70.2.2)
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


def logistic_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Newton/IRLS for L2 logistic regression; intercept (column of ones
    prepended internally) unpenalised. Returns w of shape (p+1,)."""
    n, p = X.shape
    A = np.column_stack([np.ones(n), X])
    w = np.zeros(p + 1)
    pen = np.full(p + 1, float(lam))
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


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Closed-form ridge; intercept unpenalised. Returns w of shape
    (p+1,) with w[0] the intercept."""
    n, p = X.shape
    A = np.column_stack([np.ones(n), X])
    pen = np.full(p + 1, float(lam))
    pen[0] = 0.0
    return np.linalg.solve(A.T @ A + np.diag(pen), A.T @ y)


def ridge_predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X]) @ w


def mse(pred: np.ndarray, y: np.ndarray) -> float:
    return float(((pred - y) ** 2).mean())


def select_lambda(X: np.ndarray, y: np.ndarray, kind: str
                  ) -> tuple[float, dict[float, float]]:
    """The FROZEN nested expanding-window inner split (70.2.2), on rows
    already in chronological order: three folds (fit 40/55/70%, validate
    the next 15% each); metric = mean log loss (logistic) / MSE (ridge)
    across folds; ties break toward the LARGER lambda. Standardisation
    is fit on each fold's fit rows only. Returns (lambda, per-lambda
    mean losses)."""
    n = len(y)
    losses: dict[float, float] = {}
    for lam in LAMBDA_GRID:
        fold_losses = []
        for fit_frac, val_frac in INNER_FOLDS:
            i = int(round(n * fit_frac))
            j = int(round(n * (fit_frac + val_frac)))
            if i < 2 or j <= i:
                continue
            mu, sd = standardize_fit(X[:i])
            Xf = standardize_apply(X[:i], mu, sd)
            Xv = standardize_apply(X[i:j], mu, sd)
            if kind == "logistic":
                w = logistic_fit(Xf, y[:i], lam)
                fold_losses.append(log_loss(logistic_predict(w, Xv), y[i:j]))
            elif kind == "ridge":
                w = ridge_fit(Xf, y[:i], lam)
                fold_losses.append(mse(ridge_predict(w, Xv), y[i:j]))
            else:
                raise ValueError(kind)
        losses[lam] = float(np.mean(fold_losses))
    best = None
    for lam in LAMBDA_GRID:                      # ascending: <= -> larger wins
        if best is None or losses[lam] <= losses[best]:
            best = lam
    return best, losses
