"""
Event loop and portfolio accounting.

EXECUTION TIMING — THE INVARIANT THIS FILE EXISTS TO PROTECT
------------------------------------------------------------
Signal and target weights are computed at as_of = close of day T. Orders
fill at the OPEN of day T+1, a price not visible at decision time. The loop
therefore carries the decision as `pending` and only converts it to fills
one view later, when day T+1's completed bar (and hence its open) is
legitimately readable. Filling at day T's close would mean seeing the close
and trading at it — a large spurious edge.

Position sizes are fixed in DOLLARS at decision time (weight x equity at
close of T) and converted to units at the T+1 open fill price, so no T+1
information affects how much is bought.

INTRA-STEP ORDER at the view for day D (as_of = close of D):
  1. force-settle held symbols with no bar today (delisting/data hole)
  2. mark held positions from yesterday's close to today's open
  3. 00:00 funding on the OLD book (the book that was held across midnight)
  4. fill pending targets at today's open; fees on turnover
  5. 08:00 and 16:00 funding on the NEW book
  6. mark to today's close; record equity
  7. compute the next decision from this view (skips logged, never hidden)

Equity is initial capital + cumulative price PnL - fees + net funding, in
the variation-margin style natural to perps (positions cost nothing to
open; PnL accrues on marks).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backtest import costs
from backtest.weights import Decision, SignalFn, Skip, compute_target_weights
from pitdata.store import PointInTimeStore


@dataclass(frozen=True)
class Config:
    lookback: int              # {7, 14, 28}
    skip: int                  # {0, 2}
    n_positions: int = 10
    vol_target: float = 0.20
    max_gross_leverage: float = 3.0
    beta_window: int = 60
    vol_window: int = 60
    min_quote_volume: float = 5_000_000
    initial_capital: float = 100.0
    fee_mode: str = "taker"    # "taker" | "maker"
    rebalance_ms: int = 86_400_000

    def __post_init__(self):
        if self.n_positions < 2 or self.n_positions % 2:
            raise ValueError("n_positions must be even and >= 2")
        if self.fee_mode not in ("taker", "maker"):
            raise ValueError(f"fee_mode must be taker|maker, got {self.fee_mode!r}")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")


@dataclass
class RebalanceRecord:
    ts_decision: int                       # as_of of the deciding view (close of T)
    ts_fill: int                           # as_of of the filling view (close of T+1)
    equity_at_decision: float
    raw_weights: dict[str, float]          # section 2.3 stage (test 7 target)
    final_weights: dict[str, float]
    beta_scale: float
    vol_scale: float
    est_vol_ann: float
    gross: float
    fills: dict[str, tuple[float, float]]  # sym -> (delta_units, fill_price)
    turnover_notional: float
    fees: float
    # Measured from the executed book (a second bookkeeping path, so a
    # sizing bug between decision and fill shows up as a mismatch):
    realised_gross_leverage: float         # sum(|units| * fill) / equity
    min_position_notional: float           # smallest |units| * fill taken
    binding_min_notional: float | None     # largest MIN_NOTIONAL in the book


@dataclass
class BacktestResult:
    config: Config
    timestamps: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)        # net, at each close
    gross_equity: list[float] = field(default_factory=list)  # price PnL only
    rebalances: list[RebalanceRecord] = field(default_factory=list)
    skips: list[tuple[int, str, str]] = field(default_factory=list)
    forced_liquidations: list[tuple[int, str]] = field(default_factory=list)
    n_scheduled: int = 0                   # decision opportunities
    total_fees: float = 0.0
    total_funding: float = 0.0             # signed: negative = strategy paid
    total_turnover: float = 0.0
    gross_pnl: float = 0.0
    gross_pnl_long: float = 0.0            # price PnL attributed by side
    gross_pnl_short: float = 0.0
    missing_funding_settlements: int = 0
    bankrupt: bool = False
    # Attribution trace: per-day {symbol: price PnL}, aligned with timestamps.
    pnl_by_symbol_day: list[dict[str, float]] = field(default_factory=list)

    def skip_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, reason, _ in self.skips:
            out[reason] = out.get(reason, 0) + 1
        return out


def run_backtest(
    store: PointInTimeStore,
    cfg: Config,
    start_view_ms: int,
    end_view_ms: int,
    signal_fn: SignalFn | None = None,
) -> BacktestResult:
    """
    Walk the clock over [start_view_ms, end_view_ms] in rebalance_ms steps.
    View stamps must be end-of-day (bar close) timestamps.
    """
    res = BacktestResult(config=cfg)
    positions: dict[str, float] = {}   # sym -> units (signed)
    marks: dict[str, float] = {}       # sym -> last mark price
    pending: Optional[Decision] = None
    pending_dollars: dict[str, float] = {}
    pending_ts: int = 0
    pending_equity: float = 0.0
    equity = cfg.initial_capital
    step = cfg.rebalance_ms

    day_pnl: dict[str, float] = {}

    def mark(sym: str, units: float, dprice: float) -> None:
        """Accrue price PnL, attributed to the side and symbol that earned it."""
        pnl = units * dprice
        day_pnl[sym] = day_pnl.get(sym, 0.0) + pnl
        res.gross_pnl += pnl
        if units > 0:
            res.gross_pnl_long += pnl
        else:
            res.gross_pnl_short += pnl

    # Fresh run: forget any clock position from a previous run on this store.
    # Within-run monotonicity is still enforced by iter_views.
    store.reset_clock()

    prev_as_of: Optional[int] = None
    for view in store.iter_views(start_view_ms, end_view_ms, step):
        t = view.as_of
        day_open_time = t + 1 - step  # today's bar opens where yesterday's closed
        if prev_as_of is None:
            prev_as_of = t - step

        # Today's bar for every symbol we hold or intend to trade.
        relevant = set(positions) | set(pending_dollars)
        bars = {}
        for sym in relevant:
            got = view.klines(sym, limit=1)
            bars[sym] = (
                got[-1] if got and got[-1].open_time == day_open_time else None
            )

        # 1. A held symbol with no bar today cannot be marked or traded —
        # delisting or a data hole. Settle it at its last mark, loudly.
        # No funding accrues for it today (approximation, logged).
        for sym in [s for s in positions if bars.get(s) is None]:
            notional = abs(positions[sym]) * marks[sym]
            fee = notional * costs.fee_rate(cfg.fee_mode)
            res.total_fees += fee
            res.total_turnover += notional
            res.forced_liquidations.append((t, sym))
            del positions[sym]
            del marks[sym]

        # A pending target we cannot price means the fill cannot happen as
        # decided. Abort the whole rebalance (hold the book) rather than
        # partially fill a neutral construction.
        if pending is not None and any(
            bars.get(s) is None for s in pending_dollars
        ):
            missing = [s for s in pending_dollars if bars.get(s) is None]
            res.skips.append((t, "missing_fill_bar", ",".join(missing)))
            pending, pending_dollars = None, {}

        # Funding settlements in (yesterday's close, today's close]:
        # 00:00 belongs to the book held across midnight (pre-fill);
        # 08:00/16:00 to the post-fill book. Marked at today's open — exact
        # for 00:00, the best no-lookahead approximation for the others.
        # A settlement absent from the data is counted as missing, never
        # silently zero-filled.
        boundaries = costs.settlement_times(prev_as_of, t)

        def apply_funding(book: dict[str, float], selector) -> None:
            n_expected = sum(1 for b in boundaries if selector(b))
            for sym, units in book.items():
                bar = bars[sym]
                settles = [
                    x
                    for x in costs.settlements_between(view, sym, prev_as_of, t)
                    if selector(x[0])
                ]
                if len(settles) < n_expected:
                    res.missing_funding_settlements += n_expected - len(settles)
                for _, rate in settles:
                    res.total_funding += costs.funding_cashflow(
                        units, bar.open, rate
                    )

        # 2. Mark held positions from yesterday's close to today's open.
        for sym, units in positions.items():
            mark(sym, units, bars[sym].open - marks[sym])
            marks[sym] = bars[sym].open

        # 3. 00:00 funding on the old book.
        apply_funding(positions, lambda st: st == day_open_time)

        # 4. Fill at today's open.
        if pending is not None:
            fills: dict[str, tuple[float, float]] = {}
            turnover = 0.0
            fees = 0.0
            for sym in set(positions) | set(pending_dollars):
                price = bars[sym].open
                target_units = pending_dollars.get(sym, 0.0) / price
                delta = target_units - positions.get(sym, 0.0)
                if delta == 0.0:
                    continue
                fee = costs.trade_fee(delta, price, cfg.fee_mode)
                fees += fee
                turnover += abs(delta) * price
                fills[sym] = (delta, price)
                if target_units == 0.0:
                    positions.pop(sym, None)
                    marks.pop(sym, None)
                else:
                    positions[sym] = target_units
                    marks[sym] = price
            res.total_fees += fees
            res.total_turnover += turnover
            exposures = [abs(u) * bars[s].open for s, u in positions.items()]
            res.rebalances.append(
                RebalanceRecord(
                    ts_decision=pending_ts,
                    ts_fill=t,
                    equity_at_decision=pending_equity,
                    raw_weights=pending.raw_weights,
                    final_weights=pending.final_weights,
                    beta_scale=pending.beta_scale,
                    vol_scale=pending.vol_scale,
                    est_vol_ann=pending.est_vol_ann,
                    gross=pending.gross,
                    fills=fills,
                    turnover_notional=turnover,
                    fees=fees,
                    realised_gross_leverage=sum(exposures) / pending_equity,
                    min_position_notional=min(exposures) if exposures else 0.0,
                    binding_min_notional=pending.binding_min_notional,
                )
            )
            pending, pending_dollars = None, {}

        # 5. 08:00 / 16:00 funding on the new book.
        apply_funding(positions, lambda st: st > day_open_time)

        # 6. Mark to today's close; record the daily equity point.
        for sym, units in positions.items():
            mark(sym, units, bars[sym].close - marks[sym])
            marks[sym] = bars[sym].close
        equity = (
            cfg.initial_capital + res.gross_pnl - res.total_fees
            + res.total_funding
        )
        res.pnl_by_symbol_day.append(dict(day_pnl))
        day_pnl.clear()
        res.timestamps.append(t)
        res.equity.append(equity)
        res.gross_equity.append(cfg.initial_capital + res.gross_pnl)

        if equity <= 0:
            res.bankrupt = True
            break

        # 7. Decide for tomorrow's open — except at the final view, whose
        # fill would land outside the split.
        if t + step <= end_view_ms:
            res.n_scheduled += 1
            decision = compute_target_weights(view, cfg, equity, signal_fn)
            if isinstance(decision, Skip):
                res.skips.append((t, decision.reason, decision.detail))
            else:
                pending = decision
                pending_ts = t
                pending_equity = equity
                pending_dollars = {
                    sym: w * equity
                    for sym, w in decision.final_weights.items()
                }

        prev_as_of = t

    return res
