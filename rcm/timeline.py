"""
The §60.4 event timeline, as functions instead of prose.

One horizon exists everywhere (§60.11.2): the execution-to-execution
interval [exec(D), exec(D+1)) is the holding interval for `r_shadow`,
`r_actual_price`, AND the calibration outcome. A timing mismatch between
any two of those is the Gen-1 millisecond-funding class of error, closed
here by making them the same function call.
"""

from __future__ import annotations

from dataclasses import dataclass

DAY_MS = 86_400_000
MINUTE_MS = 60_000

# §56.2 frozen fill model: execution at the open of the first 1m bar >= 00:01.
EXEC_OFFSET_MS = 1 * MINUTE_MS


def day_start_ms(day_ms: int) -> int:
    """Midnight UTC of the day containing `day_ms`."""
    return (day_ms // DAY_MS) * DAY_MS


def decision_cutoff_ms(day_ms: int) -> int:
    """The data cutoff: 00:00:00 UTC of day D. Bars with
    close_time <= this are visible; nothing later is."""
    return day_start_ms(day_ms)


def exec_time_ms(day_ms: int) -> int:
    """Entry instant: 00:01 UTC of day D (the frozen §56.2 model)."""
    return day_start_ms(day_ms) + EXEC_OFFSET_MS


@dataclass(frozen=True)
class HoldingInterval:
    start_ms: int          # exec(D)
    end_ms: int            # exec(D+1) — exclusive for prices

    @property
    def funding_window(self) -> tuple[int, int]:
        """(start, end], the §60.4 accrual window: settlements strictly after
        entry, up to and including the settlement at next entry's midnight."""
        return (self.start_ms, self.end_ms)


def holding_interval(day_ms: int) -> HoldingInterval:
    return HoldingInterval(exec_time_ms(day_ms), exec_time_ms(day_ms + DAY_MS))


def outcome_interval(signal_day_ms: int) -> HoldingInterval:
    """The calibration outcome for a signal computed on day τ: the SAME
    holding interval the strategy would have traded, [exec(τ), exec(τ+1))."""
    return holding_interval(signal_day_ms)


def outcome_admissible(signal_day_ms: int, decision_day_ms: int) -> bool:
    """§60.11.2: an observation is admissible iff its outcome interval has
    CLOSED by the decision cutoff.

    Worked example (§60.11.2.3): decision 00:00 day D. The outcome opened
    00:01 D−2 ends 00:01 D−1 <= cutoff? 00:01 D−1 < 00:00 D — yes.
    The outcome opened 00:01 D−1 ends 00:01 D > 00:00 D — excluded.
    """
    return outcome_interval(signal_day_ms).end_ms <= decision_cutoff_ms(
        decision_day_ms)


def newest_admissible_signal_day(decision_day_ms: int) -> int:
    """The most recent signal day whose outcome is admissible: D−2."""
    return day_start_ms(decision_day_ms) - 2 * DAY_MS
