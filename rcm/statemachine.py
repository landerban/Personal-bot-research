"""
§60.6 + §59.11.2 + §62.3/§62.4: calendar classification and the ONE common
transition, under the stale-book risk invariant.

CAUSAL PRECEDENCE, not control flow: a date's category is the FIRST pipeline
stage at which the intended decision became impossible —

    S1 structural-pre → S2 operational → S3 degenerate → S4 gates → S5 execution

so a day that fails gates AND has a missing execution bar is D_gate: the
decision was already dead at S4. Classification is a pure function of the
failure SET, so it is invariant under permuted check order by construction.

D_DEGENERATE (§62.4, superseding §61.3.2): a valid w = 0 is an ECONOMIC
DECISION — expected returns net of costs do not justify exposure — not a data
failure and not a formed day. Counting it as formed would let the 0.60
formation-rate criterion read 90% while exposure was held on 20% of days;
counting it as structural hides an economics outcome inside a data category.
It carries r_shadow = 0 exactly, is excluded from Δ_gate, and is reported as
its own appended tuple field.

THE §62.3 STALE-BOOK INVARIANT (superseding §61.3.1's clamp-to-G_cap, which
permitted a book formed at 0.50 gross to drift to 3.0 — Gen-1's leverage
failure by another route):

    G_ref = gross of the last successfully formed executable portfolio
    while stale:  G_t ≤ G_ref,  via the single scalar α = min(1, G_ref/G_t)

DOWNSCALE ONLY: exposure grown past its last valid scale is reduced to it;
shrunk exposure is NEVER levered back up — adding exposure without a current
valid decision is adding risk without a strategy. No deadband, no threshold.
G_cap remains the catastrophic backstop only. Lifecycle: G_ref undefined
before any formation; set at each formation; CLEARED by the M=7 forced
flatten; persisted as bot-owned state across restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

M_FLATTEN = 7                # §60.6: one third of the short momentum window


class Calendar(Enum):
    FORMED = "D_formed"
    GATE = "D_gate"
    STRUCTURAL = "D_structural"
    OPERATIONAL = "D_operational"
    DEGENERATE = "D_degenerate"        # §62.4 — the fifth category


# stage -> (causal order, category). A pure function of WHICH stages failed.
_STAGES = {
    "structural_pre": (1, Calendar.STRUCTURAL),    # universe/data/factors/meta
    "operational": (2, Calendar.OPERATIONAL),      # solver, harness, host
    "degenerate_target": (3, Calendar.DEGENERATE), # valid w = 0 (§62.4)
    "gates": (4, Calendar.GATE),
    "execution": (5, Calendar.STRUCTURAL),         # execution bar unavailable
}


def classify(failed_stages: set[str]) -> Calendar:
    """The category of the EARLIEST causal stage in the failure set."""
    unknown = failed_stages - set(_STAGES)
    if unknown:
        raise ValueError(f"unknown pipeline stages: {sorted(unknown)}")
    if not failed_stages:
        return Calendar.FORMED
    first = min(failed_stages, key=lambda s: _STAGES[s][0])
    return _STAGES[first][1]


@dataclass(frozen=True)
class Transition:
    action: str                # "form" | "hold" | "rescale" | "flatten"
    w_next: np.ndarray
    g_ref_next: float | None   # §62.3 lifecycle: set on form, cleared on flatten
    scalar: float | None = None
    consecutive_nonformed: int = 0
    note: str = ""


def transition(calendar: Calendar, w_target: np.ndarray | None,
               w_prev: np.ndarray, drifted_gross: float,
               consecutive_nonformed: int,
               g_ref: float | None) -> Transition:
    """T(calendar_state, w_prev, G_ref) — total over every category.

    One common rule for all non-formed categories (§59.11.1.2; no named
    invariant demanded a per-category deviation). On D_operational days where
    even the rescale/flatten cannot be executed, the INTENT recorded here is
    unchanged and the unexecuted action is an operational failure to report;
    the kill switch and watchdog remain the catastrophic bounds.

    `g_ref` is the caller-persisted §62.3 reference. It is None only before
    the first formation or after a forced flatten — states in which w_prev is
    flat, so no rescale is ever needed without a reference.
    """
    w_prev = np.asarray(w_prev, float)
    if calendar is Calendar.FORMED:
        if w_target is None:
            raise ValueError("formed day requires a target")
        wt = np.asarray(w_target, float)
        return Transition("form", wt,
                          g_ref_next=float(np.sum(np.abs(wt))),
                          consecutive_nonformed=0)

    k = consecutive_nonformed + 1
    if k >= M_FLATTEN:
        return Transition(
            "flatten", np.zeros_like(w_prev), g_ref_next=None,
            consecutive_nonformed=k,
            note=f"{k} consecutive non-formed days >= M={M_FLATTEN}: a book "
                 f"stale by a third of its short signal window no longer "
                 f"expresses that signal. G_ref cleared (§62.3 lifecycle); "
                 f"flat until the next formed day.")

    if g_ref is not None and drifted_gross > g_ref and drifted_gross > 0:
        scalar = g_ref / drifted_gross          # α = min(1, G_ref/G_t) < 1
        return Transition(
            "rescale", w_prev * scalar, g_ref_next=g_ref, scalar=scalar,
            consecutive_nonformed=k,
            note=f"stale gross {drifted_gross:.4f} > G_ref {g_ref:.4f}: "
                 f"single-scalar downscale to the last VALID scale (§62.3). "
                 f"Downscale only — shrunk exposure is never levered back "
                 f"up, and G_cap is a backstop, not a reference.")

    return Transition("hold", w_prev.copy(), g_ref_next=g_ref,
                      consecutive_nonformed=k,
                      note="hold: one stale day against a 21-63d signal "
                           "horizon; exposure bounded above by G_ref and in "
                           "time by M=7.")
