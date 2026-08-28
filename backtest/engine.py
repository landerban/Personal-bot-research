"""
Event loop and portfolio accounting.

EXECUTION TIMING — THE INVARIANT THIS FILE EXISTS TO PROTECT
------------------------------------------------------------
Signal and target weights are computed at as_of = close of day T. Orders
fill at the open of day T+1 PLUS `execution_delay_minutes` (Stage 2e 2:
the 00:01 open, not 00:00), a price not visible at decision time.

Filling at 00:00:00 is operationally impossible -- at that instant the
universe still has to be built, hundreds of assets ranked, betas and
covariance computed, quantities quantised and orders transmitted. Against
a MOMENTUM signal that delay is one-signed (if the move continues you buy
higher and sell lower), so it is not covered by the symmetric slippage
parameter. The 1-minute bars live in the same PIT-gated `klines` table
under interval='1m', so a 00:01 bar (close_time 00:01:59.999) still
cannot be seen by the decision taken at the previous close.

A missing 00:01 bar falls forward to 00:02, then 00:03... within
`execution_delay_minutes + 2`; if none exists the rebalance is skipped.
It NEVER falls back to 00:00, which would restore the optimistic fill. The loop
therefore carries the decision as `pending` and only converts it to fills
one view later, when day T+1's completed bar (and hence its open) is
legitimately readable. Filling at day T's close would mean seeing the close
and trading at it — a large spurious edge.

Position sizes are fixed in DOLLARS at decision time (weight x equity at
close of T) and converted to units at the T+1 open fill price, so no T+1
information affects how much is bought.

DELISTING IS NOT A DATA GAP (Stage 2e 3)
----------------------------------------
A held symbol with no bar today is one of two different events and they
must not share a code path: cross-sectional momentum shorts collapsing
coins, and collapsing coins are what delist, so this lands squarely on
the leg the strategy's profits should come from.
  * DELISTING, known from metadata whose timestamp has passed: settle at
    the exchange settlement price; if none is recorded, settle at last
    mark and log `delist_settlement_estimated`.
  * DATA GAP: hold the position and mark at the last close. Only if the
    gap exceeds `max_data_gap_days` force-settle, logging
    `data_gap_forced_exit`. Waiting is point-in-time safe -- each day
    only ever inspects that day's bar.
The two are counted separately and never merged into one event again.

INTRA-STEP ORDER at the view for day D (as_of = close of D):
  1. settle delistings; hold or force-exit symbols with no bar today
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
from backtest.weights import (
    Decision,
    RescalePlan,
    SignalFn,
    Skip,
    compute_target_weights,
    plan_rescale,
)
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
    # Stage 2d 2: capital is an input constraint set by the user, and it
    # is the solution that PRESERVES the 20% vol target -- it lowers the
    # leverage needed to clear MIN_NOTIONAL instead of raising exposure.
    # C >= 10N/L at N=10 needs L >= 0.25, against a real distribution of
    # min 0.21 / p05 0.27 / median 0.46.
    initial_capital: float = 400.0
    fee_mode: str = "taker"    # "taker" | "maker"
    rebalance_ms: int = 86_400_000
    # WITHDRAWN (Stage 2d 1). Was 1.05 under Stage 2c 3, calibrated from a
    # synthetic fixture whose unit-book vol (21%) is ~4x below the real
    # median (89%). At the real regime the floor binds constantly, pushing
    # realised vol to ~47% and expected maxDD to ~33% against a 30% kill
    # switch -- it breaks the risk budget instead of solving MIN_NOTIONAL,
    # and it amplifies every exposure failure (the pre-fix peak went 35.7x
    # -> 156.4x once it existed). Field kept, not deleted, so the
    # withdrawal is visible in every logged trial row. Test 20 pins the
    # failure mode at realistic vol.
    min_gross_leverage: float = 0.0
    # Stage 2c 4: adverse slippage per side on every fill, on top of fees.
    # Run at 0.0 and 5.0 as a sensitivity PAIR, never selected between.
    # 5bps came from n=1 synthetic testnet fill: a plausible magnitude,
    # not a measurement.
    slippage_bps_per_side: float = 0.0
    # Stage 2e 3: consecutive missing daily bars tolerated before a held
    # position is force-settled as an unrecoverable data gap.
    max_data_gap_days: int = 3
    # Stage 2e 2: minutes after the daily open at which orders fill.
    # 1 is the PRE-REGISTERED setting; 0 stays runnable so the effect is
    # measurable, but the two are never selected between on results.
    execution_delay_minutes: int = 1

    def __post_init__(self):
        if self.n_positions < 2 or self.n_positions % 2:
            raise ValueError("n_positions must be even and >= 2")
        if self.fee_mode not in ("taker", "maker"):
            raise ValueError(f"fee_mode must be taker|maker, got {self.fee_mode!r}")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0 <= self.min_gross_leverage <= self.max_gross_leverage:
            raise ValueError("min_gross_leverage must be in [0, max_gross_leverage]")
        if self.slippage_bps_per_side < 0:
            raise ValueError("slippage_bps_per_side must be >= 0")
        if self.execution_delay_minutes < 0:
            raise ValueError("execution_delay_minutes must be >= 0")


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
    beta_se_median: float                  # 2e 5: beta estimation noise
    beta_shrink_median: float


@dataclass
class RescaleRecord:
    """One rescale-on-skip execution (Stage 2c 2)."""
    ts_decision: int
    ts_fill: int
    reason: str                            # the skip reason that triggered it
    alpha: float                           # scalar applied to kept positions
    pre_gross: float                       # whole book at decision
    target_gross: float                    # planned, kept book
    post_gross: float                      # realised at the fill
    turnover_notional: float
    fees: float
    dropped: tuple[str, ...]
    units_before: dict[str, float]
    units_after: dict[str, float]
    est_vol_ann: float | None
    mode: str                              # 'vol_target' | 'cap_floor_only'


@dataclass
class BacktestResult:
    config: Config
    timestamps: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)        # net, at each close
    gross_equity: list[float] = field(default_factory=list)  # price PnL only
    rebalances: list[RebalanceRecord] = field(default_factory=list)
    skips: list[tuple[int, str, str]] = field(default_factory=list)
    # Stage 2e 3: kept separate, never merged.
    delistings: list[tuple[int, str, float, bool]] = field(default_factory=list)
    data_gap_exits: list[tuple[int, str, int]] = field(default_factory=list)
    forced_liquidations: list[tuple[int, str]] = field(default_factory=list)
    n_scheduled: int = 0                   # decision opportunities
    total_fees: float = 0.0
    total_funding: float = 0.0             # signed: negative = strategy paid
    total_turnover: float = 0.0
    gross_pnl: float = 0.0
    gross_pnl_long: float = 0.0            # price PnL attributed by side
    gross_pnl_short: float = 0.0
    missing_funding_settlements: int = 0
    # Stage 3 1.1: a raw count cannot tell 7,300 negligible exposures from
    # 200 large ones on big shorts. Funding drives most of the PnL, so the
    # gap has to be weighted by the notional actually exposed to it.
    funding_notional_expected: float = 0.0   # sum |notional| over expected settlements
    funding_notional_missing: float = 0.0    # ...over the ones with no rate
    missing_funding_rows: list[tuple[int, str, float, float, int]] = field(
        default_factory=list)                # (ts, symbol, units, notional, n_missing)
    # Stage 3a 4: every applied settlement, so funding can be sliced by
    # month, by leg, and by regime without re-deriving it from equity.
    funding_rows: list[tuple[int, str, float, float, float]] = field(
        default_factory=list)                # (ts, symbol, units, rate, amount)
    bankrupt: bool = False
    # Attribution trace: per-day {symbol: price PnL}, aligned with timestamps.
    pnl_by_symbol_day: list[dict[str, float]] = field(default_factory=list)
    # Every day, filled or not: gross notional / equity at the close. The
    # 3x cap is asserted on THIS (Test 16), not on the rebalance list.
    daily_leverage: list[tuple[int, float]] = field(default_factory=list)
    # Stage 2e 7: cheap liquidation stress. Equity implied by marking every
    # held position at the ADVERSE side of its own daily bar (low for a
    # long, high for a short) - the worst intraday path the daily data can
    # support. Not a liquidation model; a flag for configs whose
    # close-to-close results may describe a path that never happened.
    daily_worst_equity: list[tuple[int, float]] = field(default_factory=list)
    # Rescale-on-skip events (Stage 2c 2, superseding flatten-on-skip).
    rescales: list[RescaleRecord] = field(default_factory=list)
    rescale_drops: list[tuple[int, str]] = field(default_factory=list)
    total_slippage: float = 0.0            # adverse fill cost, equity terms
    minute_fill_fallbacks: int = 0         # 2e 2: 00:01 missing, used a later minute

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
    delistings: dict[str, tuple[int, float | None]] | None = None,
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
    pending_rescale: Optional[RescalePlan] = None   # from a skip, fills next open
    pending_rescale_ts: int = 0
    pending_rescale_equity: float = 0.0
    last_gross: Optional[float] = None   # 2e 1: universe filter hint
    delist_meta = delistings or {}
    missing_days: dict[str, int] = {}    # consecutive bars missing per symbol
    delisted_syms: set[str] = set()      # settled and never tradeable again
    funding_interval: dict[str, int] = {}   # 2e 6: per-symbol cadence
    MINUTE_MS = 60_000
    bps = cfg.slippage_bps_per_side
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

        # 1. Settle delistings, then hold-or-exit missing bars (Stage 2e 3).
        def settle(sym: str, price: float) -> float:
            """Close a position at `price`, charging the taker fee. Returns
            the notional traded."""
            units = positions[sym]
            pnl = units * (price - marks[sym])
            res.gross_pnl += pnl
            if units > 0:
                res.gross_pnl_long += pnl
            else:
                res.gross_pnl_short += pnl
            day_pnl[sym] = day_pnl.get(sym, 0.0) + pnl
            notional = abs(units) * price
            res.total_fees += notional * costs.fee_rate(cfg.fee_mode)
            res.total_turnover += notional
            del positions[sym]
            del marks[sym]
            missing_days.pop(sym, None)
            return notional

        # 1a. Known delistings whose settlement timestamp has passed. Settled
        # at the exchange settlement price when the metadata carries one;
        # otherwise at last mark, flagged as estimated.
        for sym in [s for s in positions if s in delist_meta]:
            settle_ts, settle_px = delist_meta[sym]
            if settle_ts > t:
                continue
            estimated = settle_px is None
            price = marks[sym] if estimated else float(settle_px)
            settle(sym, price)
            delisted_syms.add(sym)
            res.delistings.append((t, sym, price, estimated))
            res.forced_liquidations.append((t, sym))

        # 1b. No bar today and no known delisting: a data gap. Hold the
        # position, mark at the last close (settle() is not called, so no PnL
        # and no fee), and only force-exit once the gap is unrecoverable.
        for sym in [s for s in positions if bars.get(s) is None]:
            missing_days[sym] = missing_days.get(sym, 0) + 1
            if missing_days[sym] > cfg.max_data_gap_days:
                n_missing = missing_days[sym]
                settle(sym, marks[sym])
                res.data_gap_exits.append((t, sym, n_missing))
                res.forced_liquidations.append((t, sym))
        for sym in [s for s in positions if bars.get(s) is not None]:
            missing_days.pop(sym, None)

        # A pending target we cannot price means the fill cannot happen as
        # decided. Abort the whole rebalance (hold the book) rather than
        # partially fill a neutral construction.
        def fill_price_for(sym: str) -> float | None:
            """Stage 2e 2: the open of the bar `execution_delay_minutes`
            after the daily open. Falls FORWARD on a missing minute bar,
            never back to 00:00. None if no acceptable minute exists."""
            if cfg.execution_delay_minutes == 0:
                return bars[sym].open
            mins = view.klines(sym, "1m", limit=cfg.execution_delay_minutes + 3)
            by_open = {b.open_time: b for b in mins}
            for d in range(cfg.execution_delay_minutes,
                           cfg.execution_delay_minutes + 3):
                bar = by_open.get(day_open_time + d * MINUTE_MS)
                if bar is not None:
                    if d != cfg.execution_delay_minutes:
                        res.minute_fill_fallbacks += 1
                    return bar.open
            return None

        # A pending name that has delisted since the decision, has no daily
        # bar, or has no execution bar at the required minute is
        # untradeable. Aborting the whole rebalance is the same rule as
        # for a missing daily bar -- and it must be LOUD: silently
        # skipping the fill would leave a rebalance record with no fills
        # and no fees, which is how a vacuous backtest looks.
        if pending is not None and any(
            bars.get(s) is None or s in delisted_syms
            or fill_price_for(s) is None
            for s in pending_dollars
        ):
            missing = [s for s in pending_dollars
                       if bars.get(s) is None or s in delisted_syms
                       or fill_price_for(s) is None]
            res.skips.append((t, "missing_fill_bar", ",".join(missing)))
            pending, pending_dollars = None, {}
            # The book is held today; step 7 decides again at this close
            # (either a fresh rebalance, or a skip that rescales it).

        # Funding settlements in (yesterday's close, today's close]:
        # 00:00 belongs to the book held across midnight (pre-fill);
        # 08:00/16:00 to the post-fill book. Marked at today's open — exact
        # for 00:00, the best no-lookahead approximation for the others.
        # A settlement absent from the data is counted as missing, never
        # silently zero-filled.

        def apply_funding(book: dict[str, float], selector) -> None:
            for sym, units in book.items():
                # Stage 2e 6: expect settlements on THIS symbol's cadence.
                # A 4-hourly symbol settles six times a day, so scoring it
                # against a fixed 8h schedule reported spurious gaps.
                if sym not in funding_interval:
                    funding_interval[sym] = costs.infer_funding_interval_ms(
                        view, sym
                    )
                iv = funding_interval[sym]
                n_expected = sum(
                    1 for b in costs.settlement_times(prev_as_of, t, iv)
                    if selector(b)
                )
                bar = bars.get(sym)
                if bar is None:      # inside a data gap: no mark, no funding
                    continue

                # Binance stamps a settlement a few ms PAST its boundary
                # (45.7% of rows are off-boundary by 1-6ms; BTCUSDT's 00:00
                # settlement is at 00:00:00.006). Comparing the raw stamp to
                # the boundary therefore both (a) counted the 00:00
                # settlement as missing and (b) applied it to the post-fill
                # book instead of the book held across midnight, violating
                # the convention in NOTES 4. Snap each settlement to the
                # boundary it belongs to before bucketing it.
                settles = [
                    x
                    for x in costs.settlements_between(view, sym, prev_as_of, t)
                    if selector((x[0] // iv) * iv)
                ]
                notional = abs(units) * bar.open
                res.funding_notional_expected += notional * n_expected
                if len(settles) < n_expected:
                    n_miss = n_expected - len(settles)
                    res.missing_funding_settlements += n_miss
                    res.funding_notional_missing += notional * n_miss
                    res.missing_funding_rows.append(
                        (t, sym, units, notional, n_miss))
                for _, rate in settles:
                    amt = costs.funding_cashflow(units, bar.open, rate)
                    res.total_funding += amt
                    res.funding_rows.append((t, sym, units, rate, amt))

        # 2. Mark held positions from yesterday's close to today's open.
        # A symbol inside a data gap has no bar: it keeps its last mark
        # and contributes no PnL today.
        for sym, units in positions.items():
            if bars.get(sym) is None:
                continue
            mark(sym, units, bars[sym].open - marks[sym])
            marks[sym] = bars[sym].open

        # 3. 00:00 funding on the old book.
        apply_funding(positions, lambda st: st == day_open_time)

        # 4. Fill at today's open. Slippage (Stage 2c 4) makes every fill
        # adverse by `bps`: the delta is marked from its fill price back to
        # the open, so the cost lands in gross PnL and is totalled separately.
        if pending is not None:
            fills: dict[str, tuple[float, float]] = {}
            turnover = 0.0
            fees = 0.0
            for sym in set(positions) | set(pending_dollars):
                if bars.get(sym) is None:
                    # Inside a data gap: there is no market today, so this
                    # position cannot be traded. It stays held and the gap
                    # logic exits it if the gap becomes unrecoverable.
                    continue
                open_px = fill_price_for(sym)
                if open_px is None:
                    continue     # no acceptable execution bar today
                held = positions.get(sym, 0.0)
                delta_ref = pending_dollars.get(sym, 0.0) / open_px - held
                if delta_ref == 0.0:
                    continue
                price = costs.slip_price(open_px, delta_ref, bps)
                target_units = pending_dollars.get(sym, 0.0) / price
                delta = target_units - held
                fee = costs.trade_fee(delta, price, cfg.fee_mode)
                fees += fee
                turnover += abs(delta) * price
                res.total_slippage += abs(delta) * open_px * bps / 1e4
                fills[sym] = (delta, price)
                mark(sym, delta, open_px - price)   # adverse fill vs the open
                if target_units == 0.0:
                    positions.pop(sym, None)
                    marks.pop(sym, None)
                else:
                    positions[sym] = target_units
                    marks[sym] = open_px
            res.total_fees += fees
            res.total_turnover += turnover
            exposures = [
                abs(u) * (bars[s].open if bars.get(s) is not None else marks[s])
                for s, u in positions.items()
            ]
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
                    beta_se_median=pending.beta_se_median,
                    beta_shrink_median=pending.beta_shrink_median,
                )
            )
            pending, pending_dollars = None, {}
        elif pending_rescale is not None:
            # Rescale-on-skip (Stage 2c 2). The ranking could not be redone,
            # so the held book keeps its proportions and ONE scalar restores
            # the vol target within cap and floor. Positions the scalar would
            # push under MIN_NOTIONAL are closed outright (reduce-only orders
            # are floor-exempt). Deltas fill at the open with normal fees and
            # slippage. This replaces flatten-on-skip: cost scales with the
            # drift, not with book size.
            plan = pending_rescale
            before = dict(positions)
            rs_fees = 0.0
            rs_turnover = 0.0
            for sym in list(positions):
                units = positions[sym]
                target = 0.0 if sym in plan.dropped else plan.alpha * units
                delta = target - units
                if delta == 0.0 or bars.get(sym) is None:
                    continue     # cannot trade a symbol with no bar today
                open_px = fill_price_for(sym)
                if open_px is None:
                    continue
                price = costs.slip_price(open_px, delta, bps)
                rs_fees += costs.trade_fee(delta, price, cfg.fee_mode)
                rs_turnover += abs(delta) * price
                res.total_slippage += abs(delta) * open_px * bps / 1e4
                mark(sym, delta, open_px - price)
                if target == 0.0:
                    positions.pop(sym, None)
                    marks.pop(sym, None)
                else:
                    positions[sym] = target
                    marks[sym] = open_px
            res.total_fees += rs_fees
            res.total_turnover += rs_turnover
            post = sum(
                abs(u) * (bars[s].open if bars.get(s) is not None else marks[s])
                for s, u in positions.items()
            )
            res.rescales.append(
                RescaleRecord(
                    ts_decision=pending_rescale_ts,
                    ts_fill=t,
                    reason=plan.reason,
                    alpha=plan.alpha,
                    pre_gross=plan.pre_gross,
                    target_gross=plan.target_gross,
                    post_gross=post / pending_rescale_equity,
                    turnover_notional=rs_turnover,
                    fees=rs_fees,
                    dropped=plan.dropped,
                    units_before=before,
                    units_after=dict(positions),
                    est_vol_ann=plan.est_vol_ann,
                    mode=plan.mode,
                )
            )
            for sym in plan.dropped:
                res.rescale_drops.append((t, sym))
            pending_rescale = None

        # 5. 08:00 / 16:00 funding on the new book.
        apply_funding(positions, lambda st: st > day_open_time)

        # 6. Mark to today's close; record the daily equity point.
        for sym, units in positions.items():
            if bars.get(sym) is None:
                continue
            mark(sym, units, bars[sym].close - marks[sym])
            marks[sym] = bars[sym].close
        equity = (
            cfg.initial_capital + res.gross_pnl - res.total_fees
            + res.total_funding
        )
        res.pnl_by_symbol_day.append(dict(day_pnl))
        day_pnl.clear()
        gross_notional = sum(
            abs(u) * (bars[s_].close if bars.get(s_) is not None else marks[s_])
            for s_, u in positions.items()
        )
        adverse = 0.0
        for s_, u in positions.items():
            bar = bars.get(s_)
            if bar is None:
                continue
            # marks[] is today's close at this point in the step
            worst_px = bar.low if u > 0 else bar.high
            adverse += u * (worst_px - marks[s_])
        res.daily_worst_equity.append((t, equity + adverse))
        res.daily_leverage.append(
            (t, gross_notional / equity if equity > 0 else float("inf"))
        )
        res.timestamps.append(t)
        res.equity.append(equity)
        res.gross_equity.append(cfg.initial_capital + res.gross_pnl)

        if equity <= 0:
            res.bankrupt = True
            break

        # 7. Decide for tomorrow's open - except at the final view, whose
        # fill would land outside the split. A skip is logged and, when the
        # held book has drifted past the deadband, a rescale is queued for
        # tomorrow's open (Stage 2c 2) so leverage cannot run away on days
        # the strategy does not trade.
        if t + step <= end_view_ms:
            res.n_scheduled += 1
            decision = compute_target_weights(
                view, cfg, equity, signal_fn, gross_hint=last_gross
            )
            if isinstance(decision, Skip):
                res.skips.append((t, decision.reason, decision.detail))
                plan = plan_rescale(
                    view, cfg, positions, marks, equity, decision.reason
                )
                if plan is not None:
                    pending_rescale = plan
                    pending_rescale_ts = t
                    pending_rescale_equity = equity
            else:
                pending = decision
                pending_ts = t
                pending_equity = equity
                pending_dollars = {
                    sym: w * equity
                    for sym, w in decision.final_weights.items()
                }
                last_gross = decision.gross

        prev_as_of = t

    return res
