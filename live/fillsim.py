"""
Stage 16 Part C: the fill simulator -- real market data, imaginary money.

THE MODEL, FIXED IN ADVANCE (NOTES 56.2)
----------------------------------------
A paper fill executes at **the open of the next 1-minute bar after the
decision, plus 5 bps adverse slippage**. That is the backtester's own
assumption -- §2e 2 for the +1min open, §2c 4 for the 5 bps -- now applied to
live real bars instead of historical ones.

It was written down before any fill was simulated, so no later result can be
manufactured by nudging the fill model toward flattery. The 5 bps remains what
it has always been: a plausible magnitude from an n=1 synthetic fill, **not a
measurement**.

ADVERSE MEANS ADVERSE
---------------------
A buy fills ABOVE the bar open and a sell fills BELOW it, always. Against a
momentum signal the execution delay is one-signed -- if the move continues you
buy higher and sell lower -- so a symmetric-in-expectation slippage model
would flatter the strategy. The sign is not a parameter.

WHAT IS MEASURED ALONGSIDE (and adopted: nothing)
-------------------------------------------------
Real bid/ask at both the decision and the execution instant, so the realised
half-spread can be compared with the 5 bps assumption. That comparison is
EVIDENCE FOR A FUTURE STAGE, not an input to this one: the assumption is not
updated from it here, because updating a cost assumption from the same data
that produced a result is how a backtest flatters itself.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

log = logging.getLogger("live.fillsim")

# NOTES 56.2 -- do not tune.
SLIPPAGE_BPS = 5.0
EXECUTION_DELAY_MS = 60_000          # the next 1-minute bar

# Taker schedules, for reporting as a PAIR (Stage 4). Never selected between.
USDT_TAKER = 0.0005
USDC_TAKER = 0.00036

VENUE_TAG = "prod_data_sim"


@dataclass
class SimFill:
    symbol: str
    side: str                    # BUY | SELL
    qty: float
    decision_price: float        # the price the sizing assumed
    bar_open: float              # the real 1m open the fill is struck from
    fill_price: float            # bar_open adjusted adversely
    slippage_bps: float
    notional: float
    fee_usdt: float
    fee_usdc: float
    bar_open_time: int
    decision_ms: int
    # C.2 spread context -- measurement only
    bid_at_decision: float | None = None
    ask_at_decision: float | None = None
    half_spread_bps: float | None = None
    # D.1 shadow-maker counterfactual -- logged, never placed
    maker_price: float | None = None
    maker_would_fill: bool | None = None
    venue: str = VENUE_TAG

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class SimResult:
    fills: list[SimFill] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    missing_bars: list[str] = field(default_factory=list)

    @property
    def filled_units(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for f in self.fills:
            out[f.symbol] = out.get(f.symbol, 0.0) + (
                f.qty if f.side == "BUY" else -f.qty)
        return out


def adverse_fill_price(bar_open: float, side: str,
                       slippage_bps: float = SLIPPAGE_BPS) -> float:
    """Buy above the open, sell below it. Always."""
    adj = 1.0 + (slippage_bps / 1e4) * (1.0 if side == "BUY" else -1.0)
    return bar_open * adj


def execution_bar(client, symbol: str, decision_ms: int) -> tuple | None:
    """The first 1-minute bar that OPENS strictly after the decision.

    Strictly after, not at-or-after: a bar already open at decision time was
    partly formed when the decision was taken, and filling at its open would
    be filling at a price the decision could have seen.
    """
    start = decision_ms
    rows = client.klines(symbol, "1m", limit=5, start_ms=start)
    for r in rows:
        if int(r[0]) > decision_ms:
            return r
    return None


def maker_counterfactual(bar, side: str, bid: float | None,
                         ask: float | None) -> tuple[float | None, bool | None]:
    """Stage 15 D.1, upgraded to real quotes (Stage 16 C.2).

    The post-only price a maker order WOULD have quoted, and whether the real
    tape traded through it during the execution bar. Logging only -- no maker
    order is placed anywhere, and the Stage 2e rule stands: no maker-mode
    result is reportable until a fill-probability model exists. This builds
    the dataset such a model needs.
    """
    if bid is None or ask is None:
        return None, None
    price = bid if side == "BUY" else ask       # post-only rests at the touch
    low, high = float(bar[3]), float(bar[2])
    # a resting bid fills if the bar traded down to it; a resting ask if up
    would = (low <= price) if side == "BUY" else (high >= price)
    return price, bool(would)


def simulate(client, targets: dict[str, float], current: dict[str, float],
             marks: dict[str, float], decision_ms: int,
             min_notionals: dict[str, float] | None = None,
             quotes: dict[str, tuple[float, float]] | None = None,
             ) -> SimResult:
    """Simulate the fills for one rebalance against REAL bars.

    `targets`/`current` are signed unit positions. A delta whose notional is
    under the symbol's MIN_NOTIONAL is REFUSED exactly as the exchange would
    refuse it -- the simulator must not be kinder than the venue, or the paper
    book becomes one the real venue would never have allowed.
    """
    res = SimResult()
    mins = min_notionals or {}
    quotes = quotes or {}

    for symbol in sorted(set(targets) | set(current)):
        delta = targets.get(symbol, 0.0) - current.get(symbol, 0.0)
        if abs(delta) <= 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        mark = marks.get(symbol, 0.0)
        notional = abs(delta) * mark
        floor = mins.get(symbol)
        if floor is not None and notional < floor:
            res.refused.append({"symbol": symbol, "side": side,
                                "notional": notional, "min_notional": floor,
                                "reason": "below_min_notional"})
            continue

        bar = execution_bar(client, symbol, decision_ms)
        if bar is None:
            res.missing_bars.append(symbol)
            continue

        bar_open = float(bar[1])
        fill_price = adverse_fill_price(bar_open, side)
        filled_notional = abs(delta) * fill_price
        bid, ask = quotes.get(symbol, (None, None))
        half_bps = None
        if bid and ask:
            mid = (bid + ask) / 2.0
            half_bps = ((ask - bid) / 2.0 / mid) * 1e4 if mid else None
        mk_price, mk_fill = maker_counterfactual(bar, side, bid, ask)

        res.fills.append(SimFill(
            symbol=symbol, side=side, qty=abs(delta),
            decision_price=mark, bar_open=bar_open, fill_price=fill_price,
            slippage_bps=SLIPPAGE_BPS, notional=filled_notional,
            fee_usdt=filled_notional * USDT_TAKER,
            fee_usdc=filled_notional * USDC_TAKER,
            bar_open_time=int(bar[0]), decision_ms=decision_ms,
            bid_at_decision=bid, ask_at_decision=ask,
            half_spread_bps=half_bps,
            maker_price=mk_price, maker_would_fill=mk_fill,
        ))
    return res


def record(costlog, result: SimResult) -> int:
    """Write the simulated fills to the costlog, tagged `prod_data_sim` so
    they can never be mistaken for real fills or for testnet ones."""
    n = 0
    for f in result.fills:
        costlog.record_fill(
            symbol=f.symbol, side=f.side, intended_price=f.decision_price,
            fill_price=f.fill_price, qty=f.qty, fee=f.fee_usdt,
            fee_asset="USDT", maker=False, order_type="SIM_MARKET",
            ts_ms=f.bar_open_time, venue=VENUE_TAG)
        n += 1
    return n
