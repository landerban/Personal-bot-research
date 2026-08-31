"""
§60.11.1: the cadence-aware, horizon-matched funding forecast.

REUSES Gen-1's machinery and invents nothing:
  * `infer_funding_interval_ms`  — the PIT modal-gap cadence inference
  * `settlement_times`           — the forward schedule under a cadence
  * `expected_settlement_count`  — what a fully-observed window contains

THE OBSERVABILITY RULE HAS NO COUNT PARAMETER. The trailing 7-calendar-day
window is certified iff the number of observed settlements equals the number
the inferred cadence expects — an equality, not a tolerance. An uncertified
window makes the candidate UNAVAILABLE for that decision; zero funding is
never substituted (the Gen-1 §2d 5 precedent: a leg with imputed-zero funding
trades cost-free, on exactly the names momentum favours).
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest.costs import (
    expected_settlement_count,
    infer_funding_interval_ms,
    settlement_times,
)

DAY_MS = 86_400_000
LOOKBACK_MS = 7 * DAY_MS      # §60.11.1: exactly 7 calendar days, frozen


class FundingUnobservable(RuntimeError):
    """The 7-day window cannot be certified. The candidate is unavailable
    for this decision — not zero-funded, unavailable."""


@dataclass(frozen=True)
class FundingForecast:
    symbol: str
    per_settlement: float      # f̂ per settlement
    n_forward: int             # settlements inside (t_exec, t1_exec]
    total: float               # F̂ = per_settlement * n_forward
    inferred_interval_ms: int
    observed: int
    expected: int


def forecast(view, symbol: str, t_exec_ms: int, t1_exec_ms: int
             ) -> FundingForecast:
    """F̂ over the exact holding interval, under the PIT-inferred cadence.

    `view` is any object with `.as_of` and `.funding(symbol, since=...)` —
    the PITView interface. On a cadence change mid-lookback, the settlement
    SET follows the PIT timestamps (whatever actually settled is what is
    averaged) and the FORWARD schedule follows the most recent inferred
    cadence (§60.11.1).
    """
    interval = infer_funding_interval_ms(view, symbol)
    since = view.as_of - LOOKBACK_MS
    rows = view.funding(symbol, since=since)
    observed = len(rows)
    expected = expected_settlement_count(since, view.as_of, interval)
    if observed < expected or observed == 0:
        raise FundingUnobservable(
            f"{symbol}: {observed} settlements observed in the trailing 7 "
            f"days vs {expected} expected at the inferred "
            f"{interval // 3_600_000}h cadence — window not certified; "
            f"candidate unavailable (never zero-funded).")
    per = sum(r[1] for r in rows) / observed
    fwd = settlement_times(t_exec_ms, t1_exec_ms, interval)
    return FundingForecast(
        symbol=symbol, per_settlement=per, n_forward=len(fwd),
        total=per * len(fwd), inferred_interval_ms=interval,
        observed=observed, expected=expected,
    )
