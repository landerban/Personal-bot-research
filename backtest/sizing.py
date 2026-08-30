"""
Stage 17 I.3: the single source of truth for turning a weight into an order.

Backtest, fill simulator and live path each sized differently before this
existed: research computed `weight x equity` with no lot-size rounding at all,
while the live path quantized and the simulator did something in between. Three
implementations of one idea is three chances to disagree, and the disagreement
is silent because each looks correct alone.

    weight -> notional -> reference price -> raw qty
          -> step-quantized qty -> executable notional -> filter verdict

WHY QUANTIZATION IS NOT A ROUNDING DETAIL
-----------------------------------------
BTCUSDT's step is 0.001, which at ~$78,000 is ~$78 per increment. A book
sizing a $19 BTC position does not get a small BTC position -- it gets ZERO,
because the quantized quantity floors to nothing. The floor filter never even
sees it. Research sizing, which never quantized, believed that position
existed. This module is what makes the difference visible.

THE FLOOR RULE IS MEASURED, NOT ASSUMED (NOTES 57.2)
----------------------------------------------------
Probed on the venue 2026-08-30: a reduce-only sub-floor order is ACCEPTED
(partial and full close both), while the identical size WITHOUT reduce-only is
rejected -4164, "Order's notional must be no smaller than 5 (unless you choose
reduce only)". The control is what makes that conclusive rather than merely
consistent with a venue that has no floor.

So `plan_rescale` was right and `fillsim` was too strict -- under the strict
rule a position whose closing delta is sub-floor could never be closed.

**This rule is testnet-measured. Any real-money venue must be re-probed before
it is trusted** -- the same class of assumption as the §56.9 stops finding,
where a capability the code took for granted did not exist on the venue
actually in use.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

# The measured rule (NOTES 57.2). Named so it is greppable and re-verifiable
# rather than buried in an `if`.
FLOOR_RULE = "reduce_only_exempt"
FLOOR_RULE_MEASURED_ON = "testnet 2026-08-30 (tools/floor_probe.py)"

OPEN_INCREASE = "open_increase"
PARTIAL_REDUCE = "partial_reduce"
FULL_CLOSE = "full_close"
FLIP = "flip"

# Under `reduce_only_exempt`, these classes may go under the notional floor
# because they can be sent reduce-only. A FLIP crosses zero, so it cannot be
# expressed as one reduce-only order and is NOT exempt.
FLOOR_EXEMPT_CLASSES = frozenset({PARTIAL_REDUCE, FULL_CLOSE})


@dataclass(frozen=True)
class SymbolFilters:
    """What the venue will accept. `None` means the store never recorded it,
    which is treated as 'unknown' and never as 'no constraint'."""
    symbol: str
    min_notional: float | None = None
    step_size: float | None = None
    tick_size: float | None = None


@dataclass(frozen=True)
class Sized:
    symbol: str
    delta_class: str
    raw_qty: float            # before quantization
    qty: float                # after step quantization -- what would be sent
    notional: float           # executable notional at the reference price
    ok: bool
    reason: str               # "" when ok

    @property
    def quantized_away(self) -> bool:
        """True when the step size, not the floor, is what killed it."""
        return self.raw_qty > 0 and self.qty == 0


def classify_delta(current: float, target: float) -> str:
    """Which of the four kinds of order this delta is.

    The classification matters because the floor rule differs per class: a
    reduce can be sent reduce-only and is exempt; an open cannot.
    """
    if current == 0:
        return OPEN_INCREASE
    if target == 0:
        return FULL_CLOSE
    if (current > 0) != (target > 0):
        return FLIP
    return OPEN_INCREASE if abs(target) > abs(current) else PARTIAL_REDUCE


def quantize_qty(qty: float, step: float | None) -> float:
    """Floor |qty| to the lot step. Never rounds UP into a larger order than
    intended -- an order bigger than the sizing asked for is a risk decision
    nobody made."""
    if not step or step <= 0:
        return abs(qty)
    d = Decimal(str(abs(qty)))
    s = Decimal(str(step))
    return float((d / s).to_integral_value(rounding=ROUND_DOWN) * s)


def size_order(symbol: str, current_units: float, target_units: float,
               price: float, filters: SymbolFilters) -> Sized:
    """The whole pipeline for one symbol, with the venue's own constraints."""
    delta = target_units - current_units
    klass = classify_delta(current_units, target_units)
    raw = abs(delta)

    if raw <= 0 or price <= 0:
        return Sized(symbol, klass, raw, 0.0, 0.0, False, "no_delta")

    qty = quantize_qty(raw, filters.step_size)
    notional = qty * price

    if qty <= 0:
        return Sized(symbol, klass, raw, 0.0, 0.0, False,
                     f"quantized_to_zero (step {filters.step_size}, "
                     f"raw {raw:.10g})")

    floor = filters.min_notional
    if floor is not None and notional < floor:
        exempt = (FLOOR_RULE == "reduce_only_exempt"
                  and klass in FLOOR_EXEMPT_CLASSES)
        if not exempt:
            return Sized(symbol, klass, raw, qty, notional, False,
                         f"below_min_notional ({notional:.4f} < {floor})")
        return Sized(symbol, klass, raw, qty, notional, True,
                     "")  # reduce-only: floor does not apply (measured)

    return Sized(symbol, klass, raw, qty, notional, True, "")


def size_from_weight(symbol: str, weight: float, equity: float, price: float,
                     filters: SymbolFilters,
                     current_units: float = 0.0) -> Sized:
    """Convenience: weight -> target units -> `size_order`."""
    if price <= 0:
        return Sized(symbol, OPEN_INCREASE, 0.0, 0.0, 0.0, False, "no_price")
    return size_order(symbol, current_units, (weight * equity) / price,
                      price, filters)


def filters_from_view(view, symbol: str) -> SymbolFilters:
    """Read a symbol's constraints from a PIT store view.

    NOT point-in-time: Binance does not publish historical filters, so this is
    the earliest observed snapshot -- the same honest gap `PITView.min_notional`
    documents and `audit_filter_coverage()` quantifies.
    """
    mn = view.min_notional(symbol)
    step = tick = None
    getter = getattr(view, "symbol_filters", None)
    if getter is not None:
        row = getter(symbol)
        if row:
            step, tick = row.get("step_size"), row.get("tick_size")
    return SymbolFilters(symbol, mn, step, tick)
