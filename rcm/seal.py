"""
The Gen-2 seal, enforced structurally (§59.1.5).

The sealed interval [2025-01-01, 2026-07-31] is not a convention documented
in a ledger and remembered by people — it is a range check every Gen-2 data
request must pass through. The `PITView` principle applied to calendar time:
a guarantee, not a promise.

UNLOCKING requires ALL of:
  1. an explicit `UnlockToken` (no ambient flag, no environment variable),
  2. a literal `GEN2-SEAL-UNLOCK` marker present in the ledger (NOTES.md),
  3. proof the marker was COMMITTED to git BEFORE the request was made.

(3) is what defeats back-dating: the text of a ledger entry can claim any
date, but a git commit timestamp cannot be quietly rewritten. An entry
sitting uncommitted in the working tree unlocks nothing.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "NOTES.md"

# [start, end] inclusive, UTC. 2025-01-01T00:00:00Z .. 2026-07-31T23:59:59.999Z
SEAL_START_MS = 1_735_689_600_000
SEAL_END_MS = 1_785_542_399_999

UNLOCK_MARKER = "GEN2-SEAL-UNLOCK"


class SealViolation(RuntimeError):
    """A Gen-2 data request touched the sealed interval. Not a warning."""


@dataclass(frozen=True)
class UnlockToken:
    """Explicit, constructed-on-purpose unlock. Carries its own creation
    time so the ledger-first ordering can be checked against it."""
    reason: str
    created_ms: int


def _ledger_marker_commit_ms(ledger: Path = LEDGER,
                             _runner=subprocess.run) -> int | None:
    """Unix ms of the commit that INTRODUCED the unlock marker, or None.

    Uses `git log -S` (pickaxe): the marker must exist in committed history,
    not merely in the working tree. Injectable runner for tests.
    """
    try:
        out = _runner(
            ["git", "log", "-S", UNLOCK_MARKER, "--format=%ct", "--reverse",
             "--", ledger.name],
            cwd=str(ledger.parent), capture_output=True, text=True, timeout=30,
        )
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        if not lines:
            return None
        return int(lines[0]) * 1000
    except Exception:
        return None


def assert_range_allowed(start_ms: int, end_ms: int,
                         unlock: UnlockToken | None = None,
                         ledger: Path = LEDGER,
                         _now_ms=lambda: int(time.time() * 1000),
                         _runner=subprocess.run) -> None:
    """Refuse any range that intersects the seal, unless properly unlocked.

    Intersection by even one day refuses. The empty-intersection check is
    `start <= SEAL_END and end >= SEAL_START` — a range wholly inside, wholly
    covering, or merely touching the interval all intersect it.
    """
    if start_ms > end_ms:
        raise ValueError(f"bad range: start {start_ms} > end {end_ms}")
    if not (start_ms <= SEAL_END_MS and end_ms >= SEAL_START_MS):
        return  # no intersection: allowed, no unlock needed

    if unlock is None:
        raise SealViolation(
            f"request [{start_ms}, {end_ms}] intersects the SEALED interval "
            f"[{SEAL_START_MS}, {SEAL_END_MS}] (2025-01-01 .. 2026-07-31). "
            f"The seal is re-affirmed on Gen-2 grounds (NOTES 59.1); opening "
            f"the challenge set requires an explicit UnlockToken AND a "
            f"ledger entry committed beforehand — by deliberate user "
            f"decision, once, ever.")

    marker_ms = _ledger_marker_commit_ms(ledger, _runner=_runner)
    if marker_ms is None:
        raise SealViolation(
            f"unlock refused: no {UNLOCK_MARKER!r} entry exists in committed "
            f"ledger history. A working-tree entry does not count — the "
            f"entry must be committed before execution (NOTES 59.1.5).")
    if marker_ms > unlock.created_ms:
        raise SealViolation(
            f"unlock refused: the ledger entry was committed at {marker_ms} "
            f"ms, AFTER this request was created at {unlock.created_ms} ms. "
            f"The entry must precede the request — no back-dating.")
    if marker_ms > _now_ms():
        raise SealViolation("unlock refused: ledger entry timestamp is in "
                            "the future — clock or history is inconsistent.")
    # unlocked: deliberately loud, never silent
    import logging
    logging.getLogger("rcm.seal").warning(
        "SEALED INTERVAL UNLOCKED for [%d, %d]: %s (ledger entry committed "
        "%d)", start_ms, end_ms, unlock.reason, marker_ms)
