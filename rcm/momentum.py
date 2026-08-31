"""
§60.2 + §60.11.2: the residual-momentum score, the PIT-safe calibration to
return units, the shrinkage, the sign floor, and the carry-degeneration guard.

THE CONTAMINATION FIX (§60.11.2)
--------------------------------
§60.2.1 as first written pooled over all τ ≤ t. At the decision cutoff the
newest outcome interval has not closed, so the fitted slope contained future
information — non-directionally disqualifying. The set-builder here admits an
observation ONLY when its outcome interval has closed:

    D_t = { (τ, i) : outcome_end(τ, i) <= decision_cutoff(t) }

and the outcome object is the forward residual on the EXECUTION horizon —
the same [exec(τ), exec(τ+1)) interval `r_shadow` and `r_actual_price` use —
so exactly one horizon exists in the whole system.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from rcm.timeline import outcome_admissible

# §59.3.2 frozen. Lags are in RESIDUAL observations relative to the signal day.
SHORT_LAGS = (2, 21)         # inclusive
LONG_LAGS = (22, 63)         # inclusive, non-overlapping (§59.3.2)
W_SHORT, W_LONG = 0.6, 0.4
WINSOR_Z = 3.0               # house convention (manifest-flagged as such)
SHRINK_N0 = 63               # §60.2.2: one long-momentum window of evidence
CARRY_THRESHOLD = 0.5        # §60.2.3: majority-identity semantics
CARRY_WINDOW = 21            # trailing days for the guard's mean
CARRY_LABEL = "CARRY REGIME — NOT RCM"


def raw_score(residuals: np.ndarray) -> float:
    """M_i from a residual history vector (oldest first, last entry = t-1).

    Needs >= 63 residuals ending at lag 2 (i.e. len >= 64 with the newest at
    index -1 being lag 1, unused).
    """
    if len(residuals) < LONG_LAGS[1] + 1:
        raise ValueError(f"need >= {LONG_LAGS[1] + 1} residuals")
    r = residuals
    short = float(np.sum(r[-SHORT_LAGS[1] - 1:-SHORT_LAGS[0] + 1 or None]))
    # slices by lag: lag k is r[-k-1]... build explicitly to avoid off-by-one
    short = float(sum(r[-(k + 1)] for k in range(SHORT_LAGS[0], SHORT_LAGS[1] + 1)))
    long_ = float(sum(r[-(k + 1)] for k in range(LONG_LAGS[0], LONG_LAGS[1] + 1)))
    return W_SHORT * short + W_LONG * long_


def zscores(values: np.ndarray) -> np.ndarray:
    """Cross-sectional z, winsorized at ±3 (§60.2)."""
    v = np.asarray(values, float)
    sd = v.std(ddof=1) if len(v) > 1 else 0.0
    if sd <= 0:
        return np.zeros_like(v)
    return np.clip((v - v.mean()) / sd, -WINSOR_Z, WINSOR_Z)


@dataclass
class CalibrationSet:
    """The PIT-admissible pooled sample (§60.11.2). Observations are added
    with their signal day; `build` filters on outcome closure at the decision
    cutoff — an observation whose outcome has not finished DOES NOT EXIST for
    that decision."""
    z: list = field(default_factory=list)          # demeaned Z per obs
    eps_fwd: list = field(default_factory=list)    # demeaned forward residual
    signal_days: list = field(default_factory=list)
    n_cross_sections: int = 0

    @classmethod
    def build(cls, observations: list[dict], decision_day_ms: int
              ) -> "CalibrationSet":
        """observations: [{signal_day_ms, z: array, eps_fwd: array}, ...] —
        one dict per historical cross-section, arrays aligned per asset."""
        out = cls()
        for obs in observations:
            if not outcome_admissible(obs["signal_day_ms"], decision_day_ms):
                continue
            z = np.asarray(obs["z"], float)
            e = np.asarray(obs["eps_fwd"], float)
            if len(z) != len(e) or len(z) < 2:
                continue
            out.z.extend((z - z.mean()).tolist())
            out.eps_fwd.extend((e - e.mean()).tolist())
            out.signal_days.append(obs["signal_day_ms"])
            out.n_cross_sections += 1
        return out


@dataclass(frozen=True)
class Slope:
    b_hat: float               # pooled OLS slope
    b_tilde: float             # after shrinkage and the sign floor
    n_cross_sections: int
    sign_floored: bool


def calibrate(cs: CalibrationSet) -> Slope:
    """b̂ pooled; b̃ = n/(n+63)·b̂ (§60.2.2); μ_mom := 0 if b̃ <= 0 (sign
    floor — deterministic, non-presumptive)."""
    z = np.asarray(cs.z, float)
    e = np.asarray(cs.eps_fwd, float)
    denom = float(z @ z)
    b_hat = float(z @ e) / denom if denom > 0 else 0.0
    n = cs.n_cross_sections
    b_shrunk = (n / (n + SHRINK_N0)) * b_hat
    floored = b_shrunk <= 0
    return Slope(b_hat=b_hat, b_tilde=0.0 if floored else b_shrunk,
                 n_cross_sections=n, sign_floored=floored)


def mu_mom(slope: Slope, z_scores: np.ndarray) -> np.ndarray:
    return slope.b_tilde * np.asarray(z_scores, float)


@dataclass
class CarryGuard:
    """§60.2.3 + §63.1.5.2: the standing attribution that stops RCM silently
    becoming a carry trade. The label fires iff EITHER frozen condition
    holds — no new threshold, the OR of two existing rules:

      1. §60.2.3 trailing: 21-day mean s_mom < 0.5, or
      2. §63.1.A.1 exact zero momentum mass TODAY: Σ|μ_mom| = 0, or, when
         the caller supplies the canonical book, Σ|w_pre|·|μ_mom| = 0 —
         the user's decision (a): such a day TRADES, labelled, coverage N/A.

    Never a halt; never silent; absence requires both conditions false."""
    history: list = field(default_factory=list)

    def update(self, mu_momentum: np.ndarray, f_hat: np.ndarray,
               w_pre: np.ndarray | None = None) -> dict:
        m = float(np.sum(np.abs(mu_momentum)))
        f = float(np.sum(np.abs(f_hat)))
        s = m / (m + f) if (m + f) > 0 else 0.0
        self.history.append(s)
        trailing = float(np.mean(self.history[-CARRY_WINDOW:]))
        zero_mass = m == 0.0
        if w_pre is not None:
            zero_mass = zero_mass or float(
                np.sum(np.abs(np.asarray(w_pre, float))
                       * np.abs(np.asarray(mu_momentum, float)))) == 0.0
        flagged = (trailing < CARRY_THRESHOLD) or zero_mass
        return {"s_mom": s, "s_mom_trailing": trailing,
                "carry_flag": flagged, "zero_momentum_mass": zero_mass,
                "label": CARRY_LABEL if flagged else None}
