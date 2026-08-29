"""
Stage 10a §1: the harness-side status snapshot the dashboard reads.

The dashboard is a SECOND WITNESS, not a control panel: it shows what the bot
believes, from the bot's own files. One process talks to the exchange; the UI
talks to files. So the harness writes everything the UI needs here, and the UI
never parses anything the harness does not already write.

ATOMICITY
---------
Written temp-file + os.replace, which is atomic on POSIX and on Windows for
same-volume replaces. A reader therefore never sees a torn file -- it sees the
previous snapshot or the next one, never half of either. The temp file is
created in the destination directory so the rename cannot cross volumes.

THE WINDOWS CAVEAT, found by the concurrent-reader test
-------------------------------------------------------
On Windows `os.replace` raises PermissionError (WinError 5/32) when the
DESTINATION is currently open by anyone, because Python's `open()` does not
request FILE_SHARE_DELETE. POSIX has no such restriction. So a dashboard that
happens to be reading status.json at the instant the harness writes it would
make the harness's write FAIL -- rarely, nondeterministically, and in the
harness rather than in the UI, which is the worst place for it.

Both sides therefore retry over a short window: the reader's handle is open
for microseconds, so a handful of millisecond retries closes the race
completely. A write that still cannot land after RETRY_WINDOW_S RAISES; it is
never swallowed, because a status file that cannot be written is an anomaly
the operator has to see.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# The dashboard forces RED when the snapshot is older than this many cycle
# intervals (Stage 10a §3). A dashboard that stays green on a dead harness is
# worse than no dashboard.
STALE_CYCLES = 2

# Windows: os.replace over an open destination raises PermissionError.
# A reader holds status.json for microseconds, so a short retry window
# closes the race; exceeding it is a genuine fault and propagates.
RETRY_WINDOW_S = 2.0
RETRY_SLEEP_S = 0.002


@dataclass
class Position:
    symbol: str
    side: str                  # LONG | SHORT
    units: float
    notional: float
    entry: float | None
    mark: float | None
    upnl: float | None
    target_weight: float | None
    actual_weight: float | None

    @property
    def weight_gap(self) -> float | None:
        if self.target_weight is None or self.actual_weight is None:
            return None
        return self.actual_weight - self.target_weight


@dataclass
class StatusSnapshot:
    """Everything the dashboard renders. Missing fields render as 'no data
    yet', never as a crash -- day one has no history."""
    ts: float = field(default_factory=time.time)
    cycle_interval_s: float = 86_400.0
    # equity: the RE-BASELINED paper series (NOTES 46.5). A testnet balance
    # reset must never look like a drawdown.
    equity: float | None = None
    baseline_equity: float | None = None
    exchange_balance: float | None = None
    day_pnl: float | None = None
    cum_price_pnl: float | None = None
    cum_funding_pnl: float | None = None
    equity_curve: list[list[float]] = field(default_factory=list)  # [[ts, eq], ...]
    testnet_resets: list[dict] = field(default_factory=list)

    positions: list[dict] = field(default_factory=list)
    gross_leverage: float | None = None
    realised_beta: float | None = None

    kill_switch_armed: bool = False
    kill_switch_threshold: float = 0.30
    drawdown: float | None = None

    heartbeat_age_s: float | None = None
    day_counter: int = 0
    day_target: int = 28

    skips: dict[str, int] = field(default_factory=dict)
    fills_today: list[dict] = field(default_factory=list)
    funding_today: list[dict] = field(default_factory=list)

    # Stage 10 §3 -- the phase's real product
    shadow: dict = field(default_factory=dict)   # {"result": MATCH|MISMATCH|..}
    # §46.2 six criteria, live
    criteria: dict = field(default_factory=dict)
    # Stage 12 A.2 / NOTES 48.6 -- the composition guard
    composition_guard: dict = field(default_factory=dict)

    anomalies: list[dict] = field(default_factory=list)
    halted: bool = False
    halt_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_status(path: Path | str, snapshot: StatusSnapshot | dict) -> Path:
    """Atomically write the snapshot. Returns the path written.

    Never raises on a serialisation problem silently: a status file that
    cannot be written is an anomaly the operator must see, so it propagates.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict() if isinstance(snapshot, StatusSnapshot) else dict(snapshot)
    blob = json.dumps(payload, indent=1, default=str)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".status-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        # atomic same-volume rename, retried past a concurrent reader's handle
        deadline = time.monotonic() + RETRY_WINDOW_S
        while True:
            try:
                os.replace(tmp, p)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(RETRY_SLEEP_S)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def read_status(path: Path | str) -> dict | None:
    """Read a snapshot, or None if absent/unreadable/torn.

    Returning None rather than raising is deliberate: the dashboard must
    render 'no data yet' on day one and RED on a corrupt file, never a
    traceback.
    """
    p = Path(path)
    deadline = time.monotonic() + RETRY_WINDOW_S
    while True:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # Genuinely absent -- day one, or a wrong --status path. NOT a
            # race: os.replace is atomic, so the destination never briefly
            # vanishes. Returning immediately keeps the day-one page instant
            # instead of hanging for the whole retry window on every load.
            return None
        except (OSError, ValueError):
            # PermissionError (a concurrent replace on Windows) or a partial
            # parse: both transient and worth a brief retry.
            if time.monotonic() >= deadline:
                return None
        time.sleep(RETRY_SLEEP_S)


def staleness(snapshot: dict | None, now: float | None = None) -> float | None:
    """Seconds since the snapshot was written, or None if unknown."""
    if not snapshot or snapshot.get("ts") is None:
        return None
    return (time.time() if now is None else now) - float(snapshot["ts"])


def is_stale(snapshot: dict | None, now: float | None = None) -> bool:
    """True when the harness has gone quiet for more than STALE_CYCLES
    intervals -- the condition that must force the light RED."""
    age = staleness(snapshot, now)
    if age is None:
        return True
    interval = float(snapshot.get("cycle_interval_s") or 86_400.0)
    return age > STALE_CYCLES * interval
