"""
§60.5 + §60.11.4–8: the feasibility gates.

UNRESOLVED MEANS RAISE (Stage 20 ground rule 2). Two items in this module
have no value because the ledger says they have no value yet:

  * `g_min` — WITHDRAWN in §60.11.6 (the g² ≥ ½ derivation assumed
    proportional scaling that feasibility violates). The gate exists
    symbolically; evaluating it without a configured threshold raises.
  * zero momentum mass — §60.11.8 escalated the semantics to the user
    (recommendation (a): CARRY REGIME may form, coverage N/A). Until the
    user decides, the state raises.

No placeholder is ever silently supplied to make a test pass.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

N_EFF_MIN = 6.0              # per leg, §60.5 (architecture-derived, retained)


class Unresolved(RuntimeError):
    """A quantity the ledger marks UNRESOLVED was needed. The answer is a
    ledger entry, never a default in code."""


class DegenerateTarget(RuntimeError):
    """G_pre = 0 (§60.11.8.1): a named deterministic state, not a NaN."""


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
             survive_mask: np.ndarray) -> float:
    """§60.5.1 bounded coverage with S_i = |μ_mom,i| (§60.11.7, delegate-
    adopted): which part of the HYPOTHESIS survived feasibility.

    Zero denominator = zero momentum mass = §60.11.8.2, UNRESOLVED: raises
    until the user chooses (a) or (b). NaN never decides strategy state.
    """
    w = np.abs(np.asarray(w_pre, float))
    s = np.abs(np.asarray(mu_momentum, float))
    denom = float(np.sum(w * s))
    if denom == 0.0:
        raise Unresolved(
            "zero momentum mass: sum |w_pre|·|mu_mom| = 0. §60.11.8.2 "
            "escalated this state to the user (delegate recommends (a): "
            "coverage N/A, book may form flagged CARRY REGIME — NOT RCM). "
            "Raising until the ledger records the decision.")
    kept = float(np.sum(w[survive_mask] * s[survive_mask]))
    return kept / denom          # in [0, 1] by construction


@dataclass(frozen=True)
class GateConfig:
    """g_min has NO default (§60.11.6). Constructing the config without it is
    allowed; EVALUATING the exposure gate without it is not."""
    g_min: float | None = None
    n_eff_min: float = N_EFF_MIN
    c_signal_min: float = 0.50      # §60.5, majority-identity (unwithdrawn)


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    failed_gates: tuple[str, ...]
    n_eff_long: float
    n_eff_short: float
    g_ratio: float
    coverage: float


def evaluate(w_pre: np.ndarray, w_real: np.ndarray,
             mu_momentum: np.ndarray, cfg: GateConfig) -> GateVerdict:
    """All §60.5 gates on the sized book, against the canonical
    pre-feasibility book. Raises DegenerateTarget on G_pre = 0 and Unresolved
    where the ledger says so."""
    gp = g_pre(w_pre)
    if gp == 0.0:
        raise DegenerateTarget("G_pre = 0 — degenerate_target (§60.11.8.1)")
    if cfg.g_min is None:
        raise Unresolved(
            "g_min is UNRESOLVED (§60.11.6: the g² ≥ ½ derivation was "
            "withdrawn because feasibility is not a proportional rescale). "
            "Fix it in the ledger via route (a) or (b) before any real-data "
            "run; no default exists in code.")

    w_real = np.asarray(w_real, float)
    longs = np.where(w_real > 0, w_real, 0.0)
    shorts = np.where(w_real < 0, -w_real, 0.0)
    nl, ns = n_eff(longs), n_eff(shorts)
    g_ratio = float(np.sum(np.abs(w_real))) / gp
    survive = np.abs(w_real) > 0
    cov = c_signal(w_pre, mu_momentum, survive)

    failed = []
    if nl < cfg.n_eff_min:
        failed.append("n_eff_long")
    if ns < cfg.n_eff_min:
        failed.append("n_eff_short")
    if g_ratio < cfg.g_min:
        failed.append("exposure")
    if cov < cfg.c_signal_min:
        failed.append("signal_coverage")
    return GateVerdict(passed=not failed, failed_gates=tuple(failed),
                       n_eff_long=nl, n_eff_short=ns, g_ratio=g_ratio,
                       coverage=cov)
