"""
Platt calibration — the method fixed by NOTES 70.2.4 — and the reported
(never criterial) calibration diagnostics.

Frozen procedure per training window: fit the model (at the chosen
lambda) on the first 80% of the window; predict the last 20%; fit Platt
on those predictions; refit the model on 100%; apply the Platt map to
the forecast year. All inside the training window; applied point-in-time.
"""

from __future__ import annotations

import numpy as np

from g3.models import log_loss, logistic_fit, logistic_predict

CAL_FRACTION = 0.80                # frozen 80/20 split (70.2.4)
N_BINS = 10                        # frozen reporting bins


def platt_fit(scores: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Two-parameter monotone logistic map fitted on held-out-in-window
    scores. Uses the same deterministic IRLS as the model (lam=0 on the
    slope is fine at p=1: two parameters, hundreds of points)."""
    return logistic_fit(scores.reshape(-1, 1), y, lam=0.0)


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
