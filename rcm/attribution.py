"""
§59.11.3–4: the attribution deltas and the standard reporting tuple.

ONE OBJECT, TWO USES: the shadow book here IS the §60.5 coverage
denominator's canonical pre-feasibility book. No second definition exists.

    r_shadow(t+1)  = w_pre(t)ᵀ · r_price(t+1)       price-only, no costs
    Δ_gate         = E[r_shadow | formed] − E[r_shadow | gate_failed]
    Δ_transition   = E[r_actual_price − r_shadow | D_gate]

Domain: D_formed ∪ D_gate only. Both deltas carry stationary-bootstrap 90%
CIs and are NEVER significance-tested as pass/fail. The decomposition is
price-only and does NOT sum to realized net performance — the gap is
execution cost, which is not neutral across transition rules (flatten pays
to protect; hold does not), so the cost term is reported as its own line.

THE FENCE (§59.11.3.8): neither delta may tune thresholds, choose a
transition rule after seeing returns, or convert a feasibility rule into an
alpha filter. Any of those is a new strategy generation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rcm.statemachine import Calendar

DIAGNOSTIC_LABEL = ("DIAGNOSTIC — CONDITIONAL ON FORMATION — "
                    "NOT STRATEGY PERFORMANCE")
CI_LEVEL = 0.90
N_BOOT = 2000


def shadow_return(w_pre: np.ndarray, r_price_fwd: np.ndarray) -> float:
    """Price-only. Fees, slippage, quantization and funding NEVER enter."""
    return float(np.asarray(w_pre, float) @ np.asarray(r_price_fwd, float))


def _stationary_bootstrap_ci(values: np.ndarray, rng, stat=np.mean
                             ) -> tuple[float, float]:
    v = np.asarray(values, float)
    n = len(v)
    if n < 8:
        return (float("nan"), float("nan"))
    mean_block = max(2.0, n ** (1 / 3))
    p = 1.0 / mean_block
    stats = np.empty(N_BOOT)
    for b in range(N_BOOT):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(0, n)
        jumps = rng.random(n) < p
        starts = rng.integers(0, n, size=n)
        for j in range(1, n):
            idx[j] = starts[j] if jumps[j] else (idx[j - 1] + 1) % n
        stats[b] = stat(v[idx])
    lo = (1 - CI_LEVEL) / 2 * 100
    return (float(np.percentile(stats, lo)),
            float(np.percentile(stats, 100 - lo)))


@dataclass(frozen=True)
class Delta:
    point: float
    ci90: tuple[float, float]
    n_formed: int
    n_gate: int
    transition_rule: str       # ALWAYS reported beside the number (§59.11.3.7)


def delta_gate(shadow: np.ndarray, calendar: list[Calendar],
               transition_rule: str, seed: int = 17) -> Delta:
    """Were the rejected targets systematically different? Computed over
    D_formed ∪ D_gate only; other categories have no shadow target and their
    counts are the caller's to report as exclusions."""
    rng = np.random.default_rng(seed)
    s = np.asarray(shadow, float)
    cal = np.asarray([c.value for c in calendar])
    formed = s[cal == Calendar.FORMED.value]
    gated = s[cal == Calendar.GATE.value]
    point = (float(formed.mean()) if len(formed) else float("nan")) - (
        float(gated.mean()) if len(gated) else float("nan"))
    # CI on the difference via bootstrap of the concatenated labelled series
    diffs = []
    if len(formed) >= 4 and len(gated) >= 4:
        for _ in range(N_BOOT):
            f = rng.choice(formed, size=len(formed), replace=True)
            g = rng.choice(gated, size=len(gated), replace=True)
            diffs.append(f.mean() - g.mean())
        lo = (1 - CI_LEVEL) / 2 * 100
        ci = (float(np.percentile(diffs, lo)),
              float(np.percentile(diffs, 100 - lo)))
    else:
        ci = (float("nan"), float("nan"))
    return Delta(point, ci, len(formed), len(gated), transition_rule)


def delta_transition(actual_price: np.ndarray, shadow: np.ndarray,
                     calendar: list[Calendar], transition_rule: str,
                     seed: int = 18) -> Delta:
    """What did the transition rule do about it? Price-only on D_gate days —
    comparable to r_shadow by construction (identical holding interval)."""
    rng = np.random.default_rng(seed)
    cal = np.asarray([c.value for c in calendar])
    mask = cal == Calendar.GATE.value
    diff = (np.asarray(actual_price, float) - np.asarray(shadow, float))[mask]
    point = float(diff.mean()) if len(diff) else float("nan")
    ci = _stationary_bootstrap_ci(diff, rng)
    n_formed = int((cal == Calendar.FORMED.value).sum())
    return Delta(point, ci, n_formed, int(mask.sum()), transition_rule)


def reporting_tuple(calendar: list[Calendar], calendar_perf: float,
                    gate_counts: dict[str, int]) -> dict:
    """§59.11.4: the six mandatory fields of every performance row."""
    n = len(calendar)
    counts = {c: sum(1 for x in calendar if x is c) for c in Calendar}
    return {
        "calendar_performance": calendar_perf,          # full calendar, §59.4.1
        "formation_rate": counts[Calendar.FORMED] / n if n else 0.0,
        "gate_skip_rate": counts[Calendar.GATE] / n if n else 0.0,
        "structural_skip_rate": counts[Calendar.STRUCTURAL] / n if n else 0.0,
        "operational_skip_rate": counts[Calendar.OPERATIONAL] / n if n else 0.0,
        "gate_composition": dict(gate_counts),
    }


def formed_days_metric(value: float, name: str) -> dict:
    """Any formed-days-only number MUST carry the literal label."""
    return {"metric": name, "value": value, "label": DIAGNOSTIC_LABEL}
