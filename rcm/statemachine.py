"""
§60.6 + §59.11.2: the calendar classifier and the ONE common transition.

CAUSAL PRECEDENCE, not control flow: a date's category is the FIRST pipeline
stage at which the intended decision became impossible —

    S1 structural-pre  →  S2 operational  →  S3 gates  →  S4 execution

so a day that fails gates AND has a missing execution bar is D_gate: the
decision was already dead at S3. Classification is invariant under permuted
check order because the mapping is a pure function of the failure SET.

THE TRANSITION is one common rule for every non-formed category (§60.6):
hold, single-scalar rescale if drifted gross exceeds the cap, forced flatten
on the 7th consecutive non-formed day. Two symbols §60.6 referenced needed
mechanical resolution after §60.11's corrections, both recorded in §61 and
vetoable:
  * the rescale target: §60.6 said "restore gross to G_target", a symbol
    §60.11.3 withdrew. Implemented as clamp-to-G_CAP — the minimal
    intervention that enforces the cap without re-deciding anything.
  * degenerate_target's calendar category: D_structural — because
    §59.11.3.3's shadow domain is defined by "a shadow target exists", and a
    zero target is exactly a day on which none does. Mapping it to D_gate
    would inject meaningless zeros into Δ_gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from rcm.optimizer import G_CAP

M_FLATTEN = 7                # §60.6: one third of the short momentum window


class Calendar(Enum):
    FORMED = "D_formed"
    GATE = "D_gate"
    STRUCTURAL = "D_structural"
    OPERATIONAL = "D_operational"


# stage -> (order, category). Category is a pure function of WHICH stages
# failed, never of the order in which the caller happened to check them.
_STAGES = {
    "structural_pre": (1, Calendar.STRUCTURAL),   # universe/data/factors/meta
    "operational": (2, Calendar.OPERATIONAL),     # solver, harness, host
    "degenerate_target": (3, Calendar.STRUCTURAL),  # G_pre = 0 (§60.11.8.1)
    "gates": (4, Calendar.GATE),
    "execution": (5, Calendar.STRUCTURAL),        # execution bar unavailable
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
    scalar: float | None = None
    consecutive_nonformed: int = 0
    note: str = ""


def transition(calendar: Calendar, w_target: np.ndarray | None,
               w_prev: np.ndarray, drifted_gross: float,
               consecutive_nonformed: int) -> Transition:
    """T(calendar_state, w_prev) — total over every category (§60.6).

    On a FORMED day: adopt the target; counter resets. On ANY non-formed day
    the same common rule applies; no per-category behaviour exists because no
    named invariant demanded one (§59.11.1.2). On D_operational days where
    even the rescale/flatten cannot be executed, the INTENT recorded here is
    unchanged and the unexecuted action is an operational failure to report —
    the kill switch and watchdog remain the catastrophic bounds.
    """
    w_prev = np.asarray(w_prev, float)
    if calendar is Calendar.FORMED:
        if w_target is None:
            raise ValueError("formed day requires a target")
        return Transition("form", np.asarray(w_target, float),
                          consecutive_nonformed=0)

    k = consecutive_nonformed + 1
    if k >= M_FLATTEN:
        return Transition(
            "flatten", np.zeros_like(w_prev), consecutive_nonformed=k,
            note=f"{k} consecutive non-formed days >= M={M_FLATTEN}: a book "
                 f"stale by a third of its short signal window no longer "
                 f"expresses that signal (§60.6). Flat until a formed day.")
    if drifted_gross > G_CAP:
        scalar = G_CAP / drifted_gross
        return Transition(
            "rescale", w_prev * scalar, scalar=scalar,
            consecutive_nonformed=k,
            note=f"drifted gross {drifted_gross:.3f} > cap {G_CAP}: single-"
                 f"scalar clamp to the cap — risk changes, selection does "
                 f"not. (Rescale target: see §61 — §60.6's 'G_target' was "
                 f"withdrawn by §60.11.3.)")
    return Transition("hold", w_prev.copy(), consecutive_nonformed=k,
                      note="hold: one stale day against a 21-63d signal "
                           "horizon; leverage drift bounded by the cap "
                           "clamp and in time by M=7.")
