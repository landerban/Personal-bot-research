"""
Position and order state from the exchange — the only source of truth.

On startup and after any reconnect the trader calls fetch_state() and plans
from it. There is no local position file to trust; a local file goes stale
and lies precisely when it matters (after a crash).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from live.client import TestnetClient


@dataclass
class ExchangeState:
    positions: dict[str, float]          # symbol -> signed units (non-zero only)
    open_orders: list[dict]
    equity: float                        # totalMarginBalance
    fetched_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


def fetch_state(client: TestnetClient) -> ExchangeState:
    """Positions, open orders and equity straight from the exchange."""
    return ExchangeState(
        positions=client.positions(),
        open_orders=client.open_orders(),
        equity=client.equity(),
    )


def unknown_positions(state: ExchangeState, allowed: set[str]) -> dict[str, float]:
    """Positions in symbols this bot does not manage. Their presence at
    startup is a halt condition unless the operator opts to flatten them."""
    return {s: u for s, u in state.positions.items() if s not in allowed}


def plan_deltas(current: dict[str, float], target: dict[str, float]) -> dict[str, float]:
    """
    Units to trade per symbol so that current + delta == target. Symbols
    held but absent from the target are closed. Zero deltas are omitted.

    Because `current` is always fetched from the exchange, a restart with
    positions already at target produces an empty plan — no double
    positioning.
    """
    out = {}
    for sym in set(current) | set(target):
        d = target.get(sym, 0.0) - current.get(sym, 0.0)
        if d != 0.0:
            out[sym] = d
    return out
