"""
Stage 17 I.1: `await_reconciled_state` -- the one settle primitive.

THE BUG THIS REPLACES
---------------------
§56.8-3 measured `positionRisk` returning 0.0 immediately after a FILLED
market order, and correct on a later call. Two call sites read state after
placing orders and they behaved differently:

  * tools/roundtrip_demo.py  polled with a bare `time.sleep(0.5)`
  * live/phase2.py           read once, immediately, with no settle at all

The second is the dangerous one: the §52.4 atomicity check compares the
filled book against target, so a lagged read shows missing legs and fires a
FALSE breach -- the system would report a defect that did not happen, and
repair a book that was already correct.

WHY A PRIMITIVE AND NOT A SLEEP
-------------------------------
A sleep encodes a guess about how slow the venue is. This waits for a
CONDITION and reports which one was met:

  * every confirmed fill is reflected in positions, within step tolerance, or
  * the observed position deltas match what was expected

and RAISES on the deadline rather than returning a state it does not believe.
A settle that silently gives up is a sleep with extra steps.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from live import reconcile

log = logging.getLogger("live.settle")

DEFAULT_TIMEOUT_S = 15.0
DEFAULT_POLL_S = 0.5


class SettleTimeout(RuntimeError):
    """The exchange never reflected the expected fills. NOT swallowed: an
    unsettled book must not be silently treated as a settled one."""


@dataclass
class SettleResult:
    state: object                      # reconcile.ExchangeState
    polls: int
    elapsed_s: float
    settled: bool
    detail: str = ""
    observed: dict = field(default_factory=dict)


def _matches(observed: dict[str, float], expected: dict[str, float],
             tolerances: dict[str, float]) -> tuple[bool, str]:
    """Every expected position reflected within that symbol's step size."""
    for sym, want in expected.items():
        got = observed.get(sym, 0.0)
        tol = max(tolerances.get(sym, 0.0), 1e-12) * 1.5
        if abs(got - want) > tol:
            return False, f"{sym}: exchange {got} vs expected {want} (tol {tol})"
    return True, ""


def await_reconciled_state(client, expected_positions: dict[str, float],
                           step_sizes: dict[str, float] | None = None,
                           timeout_s: float = DEFAULT_TIMEOUT_S,
                           poll_interval_s: float = DEFAULT_POLL_S,
                           sleeper=time.sleep, clock=time.monotonic
                           ) -> SettleResult:
    """Poll until the exchange reflects `expected_positions`, or raise.

    `expected_positions` is the book the caller believes it just created --
    signed units per symbol, omitting anything expected to be flat.
    `step_sizes` supplies per-symbol tolerance; a symbol without one is
    matched near-exactly.

    Returns as soon as the condition holds. Raises `SettleTimeout` on the
    deadline, with the first mismatch named, so the caller can distinguish
    "the venue was slow" from "a leg genuinely did not fill" -- which is
    exactly the distinction the atomicity check needs and could not make.
    """
    steps = step_sizes or {}
    start = clock()
    polls = 0
    last = ""
    while True:
        state = reconcile.fetch_state(client)
        polls += 1
        ok, detail = _matches(state.positions, expected_positions, steps)
        if ok:
            elapsed = clock() - start
            if polls > 1:
                log.info("state settled after %d polls (%.1fs)", polls, elapsed)
            return SettleResult(state, polls, elapsed, True,
                                observed=dict(state.positions))
        last = detail
        if clock() - start >= timeout_s:
            raise SettleTimeout(
                f"exchange state did not settle in {timeout_s:.0f}s after "
                f"{polls} polls: {last}. NOT treating this as a settled book -- "
                f"an atomicity verdict on unsettled state would be a guess.")
        sleeper(poll_interval_s)
