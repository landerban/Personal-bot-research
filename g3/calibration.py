"""
Platt calibration — the method fixed by NOTES 70.6.8 — and the reported
(never criterial) calibration diagnostics.

Frozen procedure per training window (the calibrator is NEVER fitted to
in-sample fitted probabilities): after penalty selection, refit at the
chosen value on each 70.6.7 inner fold's fit span and predict its
validation span; fit Platt on the concatenated time-respecting
OUT-OF-FOLD predictions; fit the final model on the full window;
forecast the target year; apply the already-fitted calibrator.
"""

from __future__ import annotations

import numpy as np

from g3.models import (log_loss, logistic_fit, logistic_predict,
                       standardize_apply, standardize_fit)

N_BINS = 10                        # frozen reporting bins


def platt_fit(scores: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Two-parameter monotone logistic map fitted on OUT-OF-FOLD scores.
    Uses the same deterministic IRLS as the model (C=0 disables the
    penalty: two parameters, hundreds of points)."""
    return logistic_fit(scores.reshape(-1, 1), y, C=0.0)


def oof_predictions(X: np.ndarray, y: np.ndarray, chosen: float,
                    folds: list[tuple[list[int], list[int]]],
                    fit_fn, predict_fn
                    ) -> tuple[np.ndarray, np.ndarray]:
    """The 70.6.8 OOF pass: refit at the CHOSEN penalty on each inner
    fold's fit span, predict its validation span, concatenate in time
    order. Returns (oof_scores, oof_targets)."""
    scores, targets = [], []
    for fit_idx, val_idx in folds:
        mu, sd = standardize_fit(X[fit_idx])
        w = fit_fn(standardize_apply(X[fit_idx], mu, sd), y[fit_idx],
                   chosen)
        scores.append(predict_fn(w, standardize_apply(X[val_idx], mu, sd)))
        targets.append(y[val_idx])
    return np.concatenate(scores), np.concatenate(targets)


def fit_calibrator_oof(X: np.ndarray, y: np.ndarray, chosen: float,
                       folds: list[tuple[list[int], list[int]]]
                       ) -> np.ndarray:
    """Platt fitted on the OOF predictions of the direction model."""
    s, t = oof_predictions(X, y, chosen, folds,
                           logistic_fit, logistic_predict)
    return platt_fit(s, t)


def platt_apply(ab: np.ndarray, scores: np.ndarray) -> np.ndarray:
    return logistic_predict(ab, scores.reshape(-1, 1))


def reliability_report(p: np.ndarray, y: np.ndarray,
                       n_bins: int = N_BINS) -> dict:
    """Reliability curve on equal-width bins + the binned Brier
    decomposition (reliability - resolution + uncertainty). REPORTED,
    never a criterion (70.2.4)."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    curve = []
    n = len(p)
    ybar = float(y.mean()) if n else float("nan")
    reliability = resolution = 0.0
    for b in range(n_bins):
        m = idx == b
        cnt = int(m.sum())
        if cnt == 0:
            curve.append({"bin": b, "n": 0, "p_mean": None, "y_rate": None})
            continue
        pm, yr = float(p[m].mean()), float(y[m].mean())
        curve.append({"bin": b, "n": cnt, "p_mean": round(pm, 6),
                      "y_rate": round(yr, 6)})
        reliability += cnt / n * (pm - yr) ** 2
        resolution += cnt / n * (yr - ybar) ** 2
    uncertainty = ybar * (1 - ybar)
    return {"curve": curve,
            "brier_reliability": round(reliability, 8),
            "brier_resolution": round(resolution, 8),
            "brier_uncertainty": round(uncertainty, 8),
            "brier_binned": round(reliability - resolution + uncertainty, 8),
            "log_loss": round(log_loss(p, y), 8) if n else None,
            "n": n}
