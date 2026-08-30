"""
Stage 12 Part B: the Phase-2 paper cycle — the frozen config on testnet.

TESTNET ONLY. Every request goes through TestnetClient, which cannot be
pointed at a production venue. Real-money trading remains gated on the
holdout decision (NOTES 49.3).

THE CYCLE
---------
    fetch -> decide -> execute -> reconcile -> shadow -> report -> status.json

`decide` runs the RESEARCH function, `backtest.weights.compute_target_weights`,
over an in-memory point-in-time store built from testnet REST data
(live/pitfeed.py). Strategy identity therefore holds by construction rather
than by comparison.

`shadow` (STAGE10 §3 / NOTES 46.3) is what remains genuinely falsifiable once
identity is structural: the decision is recomputed from an INDEPENDENT re-fetch
of the bars and compared weight-by-weight to 1e-6, and the target book is
compared against what actually filled. Comparing compute_target_weights to
itself would be the Stage 2e vacuity trap -- a check that passes because it
cannot fail -- and is not performed or claimed.

NOTHING HERE MAY TUNE THE STRATEGY (NOTES 46.7). Every parameter below is the
frozen one; the only venue-driven quantity is which symbols testnet lists.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backtest import weights as W
from backtest.engine import Config
from backtest.universe_filter import filter_universe
from backtest.weights import LIQUIDITY_RANK_WINDOW
from live import reconcile
from live.client import FilterRejected, TestnetClient
from live.fixes import (
    check_atomicity, book_weights, detect_stop_fills,
    place_order_idempotent, reconcile_funding, reconstruct_position_at,
)
from live.pitfeed import LiveFeed

log = logging.getLogger("live.phase2")

ROOT = Path(__file__).resolve().parents[1]
DAY_MS = 86_400_000
BTC = W.BTC

# NOTES 49.4 limitation 2: pre-narrow before fetching klines. 40 against a
# top-15 rule. Not a strategy parameter -- a REST budget.
SHORTLIST = 40
# NOTES 55.3: once the store's volume reference is older than this the
# dashboard composition line goes AMBER. Nothing fetches volumes in-cycle
# from any new source -- the store IS the reference.
VOLUME_STALE_DAYS = 60
# Shadow tolerance, STAGE10 §3.
WEIGHT_TOL = 1e-6
# Exchange-side reduce-only stop distance. SAFETY, not strategy: it is a
# backstop that survives this process, this machine and this ISP, and it
# is deliberately far enough out that it never front-runs the vol target.
STOP_PCT = 0.20


@lru_cache(maxsize=1)
def production_volumes(db: str | None = None) -> tuple[dict[str, float], float]:
    """(symbol -> production median quote volume, age of the reference in days).

    Read ONCE from the frozen research store, by the same point-in-time
    measure `compute_target_weights` ranks on: median quote volume over the
    trailing LIQUIDITY_RANK_WINDOW days at the store's latest date.

    Why the store and not the venue (NOTES 55.1): testnet quote volume is
    synthetic. The universe rule says "median quote volume", and the store
    holds that quantity for the real market. Nothing here fetches volumes
    in-cycle from any new source -- and the funding-presence filter still uses
    the live 14-day funding feed for today's candidacy, which is why replays
    older than that window are unsupported (the withdrawn artifact of 53.1).

    The reference ages. Its age is returned so the caller can raise the
    NOTES 55.3 staleness alert; it is never silently trusted.
    """
    from statistics import median

    from backtest import runner as _runner
    from pitdata.store import PointInTimeStore

    path = db or str(ROOT / "xsmom.db")
    store = PointInTimeStore(path, read_only=True)
    try:
        end = _runner.data_end_ms(store)
        store.reset_clock()
        view = store.view_as_of(end)
        out: dict[str, float] = {}
        for sym in view.universe(min_quote_volume=0.0):
            bars = view.klines(sym, "1d", limit=LIQUIDITY_RANK_WINDOW)
            if len(bars) >= LIQUIDITY_RANK_WINDOW:
                out[sym] = float(median(b.quote_volume for b in bars))
    finally:
        store.close()
    age_days = (time.time() * 1000 - end) / DAY_MS
    log.info("production volume reference: %d symbols, %.0f days old",
             len(out), age_days)
    return out, age_days


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class Phase2Config:
    """The FROZEN deployment config. NOTES 45.13 / 48.1. Do not tune."""
    capital: float = 800.0
    vol_target: float = 0.10
    lookback: int = 14
    skip: int = 0
    n_positions: int = 10
    max_liquidity_rank: int = 15
    rank_buffer: int = 0
    max_gross: float = 3.0
    fee_mode: str = "taker"
    kill_switch_dd: float = 0.30
    day_target: int = 28
    shortlist: int = SHORTLIST

    def to_backtest_config(self) -> Config:
        return Config(
            lookback=self.lookback, skip=self.skip,
            n_positions=self.n_positions, vol_target=self.vol_target,
            max_gross_leverage=self.max_gross,
            initial_capital=self.capital, fee_mode=self.fee_mode,
            max_liquidity_rank=self.max_liquidity_rank,
            rank_buffer=self.rank_buffer,
            # cost assumptions are backtest-only; live pays real fees
            slippage_bps_per_side=0.0,
        )


@dataclass
class CycleResult:
    ts: float
    universe: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    guard_alert: bool = False
    guard_reason: str | None = None
    decision: dict | None = None          # symbol -> final weight
    skip_reason: str | None = None
    target_units: dict = field(default_factory=dict)
    deltas: dict = field(default_factory=dict)
    filled: dict = field(default_factory=dict)
    shadow: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    equity: float | None = None
    atomicity: dict = field(default_factory=dict)     # STAGE10 4.1
    stops_placed: list[str] = field(default_factory=list)
    stops_unsupported: bool = False    # NOTES 56.7: venue capability
    stop_cascade: list[str] = field(default_factory=list)  # 4.2
    funding: dict = field(default_factory=dict)       # 4.3 + criterion 2
    price_pnl: float = 0.0
    fees: float = 0.0


class Phase2Runner:
    """One paper cycle of the frozen config against testnet."""

    def __init__(self, client: TestnetClient, cfg: Phase2Config | None = None,
                 clock=time.time):
        self.c = client
        self.cfg = cfg or Phase2Config()
        self._clock = clock
        self.feed = LiveFeed(client, clock=clock)
        self.volume_ref_age_days = 0.0

    # ---------------------------------------------------------- universe

    def candidate_symbols(self) -> tuple[list[str], list[dict], dict]:
        """(shortlist, excluded verdicts, guard info).

        Crypto-only (NOTES 48) among testnet's USDT perpetuals, pre-narrowed
        to the top `shortlist` by 24h quote volume so the kline fetch stays a
        sane number of requests (NOTES 49.4 limitation 2).
        """
        info = self.c.request("GET", "/fapi/v1/exchangeInfo")
        listed = [s["symbol"] for s in info.get("symbols", [])
                  if s.get("status") == "TRADING"
                  and s.get("quoteAsset") == "USDT"]

        # NOTES 55.1: rank by PRODUCTION median quote volume from the research
        # store, not by testnet's synthetic 24h volume.
        #
        # 48.1's rule is "top 15 by point-in-time MEDIAN QUOTE VOLUME". Testnet
        # volume is not that quantity: 53.2 measured it ranking junk above real
        # majors, which starved beta identification and produced 0 books on 12
        # of 12 replay days. Ranking by the real number is a more faithful
        # implementation of the unchanged rule, not a new rule.
        vol, self.volume_ref_age_days = production_volumes()
        ranked = sorted([s for s in listed if s in vol],
                        key=lambda s: -vol[s])
        # Anything the store has never priced cannot be ranked by the rule, so
        # it is appended behind everything it can rank -- never silently
        # promoted, never silently dropped.
        unranked = sorted(s for s in listed if s not in vol)
        ranked += unranked

        # The universe and the metadata come from the SAME venue here, so the
        # §48.11 recency hole cannot open: anything testnet lists, testnet's
        # exchangeInfo describes. No last_bar_ms is needed.
        keep, dropped = filter_universe(ranked)
        shortlist = keep[:self.cfg.shortlist]
        if BTC not in shortlist:
            shortlist.append(BTC)       # beta reference, position or not

        # NOTES 48.6 composition guard: observe and alert, never auto-amend.
        top15_unfiltered = ranked[:15]
        in_top15 = [v for v in dropped if v.symbol in top15_unfiltered]
        ambiguous = [v for v in dropped if v.klass == "ambiguous"]
        alert = bool(ambiguous) or len(in_top15) >= 3
        reason = None
        if alert:
            bits = []
            if ambiguous:
                bits.append(f"{len(ambiguous)} underlying_ambiguous")
            if len(in_top15) >= 3:
                bits.append(f"{len(in_top15)} excluded in the unfiltered top-15")
            reason = "; ".join(bits)
        stale = self.volume_ref_age_days > VOLUME_STALE_DAYS
        if stale:
            alert = True
            reason = ((reason + "; ") if reason else "") + (
                f"volume reference stale ({self.volume_ref_age_days:.0f}d "
                f"> {VOLUME_STALE_DAYS}d) -- refresh store")
        guard = {
            "alert": alert, "reason": reason,
            "volume_ref_age_days": round(self.volume_ref_age_days, 1),
            "volume_ref_stale": stale,
            "n_ranked_by_production_volume": len(ranked) - len(unranked),
            "n_unranked": len(unranked),
            "excluded": [{"symbol": v.symbol, "reason": v.reason}
                         for v in dropped[:50]],
            "ambiguous": [{"symbol": v.symbol, "reason": v.reason}
                          for v in ambiguous],
            "excluded_in_top15": len(in_top15),
            "n_listed": len(listed), "n_eligible": len(keep),
        }
        return shortlist, guard["excluded"], guard

    # ---------------------------------------------------------- decision

    def decide(self, symbols: list[str], equity: float, as_of: int | None = None):
        """Run the RESEARCH pipeline on live data. Returns (decision|Skip,
        feed snapshot)."""
        # NOTES 55.1: the store carries PRODUCTION liquidity so the
        # max_liquidity_rank cap inside compute_target_weights ranks on the
        # real measure. Without this the cap silently reverts to testnet's
        # synthetic volume.
        vol, _ = production_volumes()
        view, snap = self.feed.build(symbols, as_of=as_of,
                                     volume_reference=vol)
        cfg = self.cfg.to_backtest_config()
        out = W.compute_target_weights(view, cfg, equity)
        return out, snap

    # ------------------------------------------------------------ shadow

    def shadow_check(self, symbols: list[str], equity: float,
                     decision, filled_weights: dict | None = None) -> dict:
        """STAGE10 §3 / NOTES 46.3.

        Two comparisons that CAN fail:
          1. the decision recomputed from an INDEPENDENT re-fetch of the bars
             -- catches staleness, a partial page, a listing change mid-cycle
          2. target weights against the weights actually FILLED
             -- catches execution divergence (also STAGE10 §4.1)
        """
        res: dict = {"result": "n/a", "max_weight_delta": None, "detail": ""}
        if isinstance(decision, W.Skip):
            res.update(result="SKIP", detail=f"decision skipped: {decision.reason}")
            return res
        try:
            shadow_feed = LiveFeed(self.c, clock=self._clock)
            vol, _ = production_volumes()
            view2, snap2 = shadow_feed.build(symbols, volume_reference=vol)
            cfg = self.cfg.to_backtest_config()
            again = W.compute_target_weights(view2, cfg, equity)
            shadow_feed.close()
        except Exception as e:                       # pragma: no cover
            res.update(result="ERROR", detail=f"{type(e).__name__}: {e}")
            return res

        if isinstance(again, W.Skip):
            res.update(result="MISMATCH",
                       detail=f"re-decide skipped ({again.reason}) where the "
                              f"live decision produced a book")
            return res

        a, b = decision.final_weights, again.final_weights
        syms = set(a) | set(b)
        deltas = {s: abs(a.get(s, 0.0) - b.get(s, 0.0)) for s in syms}
        worst = max(deltas.values()) if deltas else 0.0
        res["max_weight_delta"] = worst
        if set(a) != set(b):
            res.update(result="MISMATCH",
                       detail=f"different names: live {sorted(set(a) - set(b))} "
                              f"vs shadow {sorted(set(b) - set(a))}")
            return res
        if worst > WEIGHT_TOL:
            off = max(deltas, key=deltas.get)
            res.update(result="MISMATCH",
                       detail=f"{off} {a[off]:.9f} vs {b[off]:.9f} "
                              f"(delta {worst:.3e} > {WEIGHT_TOL:g})")
            return res

        res.update(result="MATCH",
                   detail=f"{len(a)} names agree to {WEIGHT_TOL:g}")
        if filled_weights:
            fd = {s: abs(a.get(s, 0.0) - filled_weights.get(s, 0.0)) for s in a}
            res["max_fill_delta"] = max(fd.values()) if fd else 0.0
        return res

    # ----------------------------------------------------------- execute

    def target_units(self, decision, equity: float,
                     filters) -> tuple[dict, list[str]]:
        """Signed units per symbol, quantised to the venue's lot size."""
        from decimal import Decimal

        from live.client import quantize_qty
        units, problems = {}, []
        for sym, w in decision.final_weights.items():
            f = filters.get(sym)
            if f is None:
                problems.append(f"{sym}: no exchange filters")
                continue
            bid, ask = self.c.book(sym)
            mid = (bid + ask) / 2.0
            if mid <= 0:
                problems.append(f"{sym}: no book")
                continue
            dollars = abs(w) * equity
            qty = quantize_qty(dollars / mid, f.step_size)
            notional = float(qty) * mid
            if qty < f.min_qty or Decimal(str(notional)) < f.min_notional:
                problems.append(
                    f"{sym}: {notional:.2f} < MIN_NOTIONAL {f.min_notional}")
                continue
            units[sym] = float(qty) * (1.0 if w > 0 else -1.0)
        return units, problems


def order_id(tag: str, sym: str, clock=time.time) -> str:
    """Deterministic-per-second client id, so a retry after an ambiguous POST
    can query the SAME id rather than risk a duplicate (STAGE10 §4.4)."""
    return f"p2-{tag}-{sym}-{int(clock())}"[:36]


# Binance: "Order type not supported for this endpoint."
CODE_ORDER_TYPE_UNSUPPORTED = -4120


def _place_stops(runner: "Phase2Runner", positions: dict, res: CycleResult
                 ) -> list[str]:
    """Layer 1 protection (STAGE10 4.2): an exchange-side reduce-only stop per
    position, replaced every rebalance.

    It survives this process, this machine and this ISP -- which is the point.
    The distance is deliberately wide: a backstop, never a strategy exit.

    LAYER 1 IS UNAVAILABLE ON THE TESTNET VENUE (NOTES 56.7). Stage 16 Part A
    established that this venue refuses EVERY conditional order type on
    /fapi/v1/order with -4120 -- STOP_MARKET, TAKE_PROFIT_MARKET, with or
    without closePosition or reduceOnly -- even though exchangeInfo.orderTypes
    advertises them all. §52.4 claimed stops were being placed; on this venue
    they cannot be, and because no book ever formed the claim was never
    exercised.

    So a -4120 is recorded ONCE as a venue capability limitation rather than
    as a per-symbol error every cycle (the §51.10 spam lesson). Layers 2
    (watchdog) and 3 (kill switch) are unaffected and remain armed.
    """
    from live.client import quantize_price

    placed = []
    filters = runner.c.filters()
    for sym, units in positions.items():
        if not units:
            continue
        f = filters.get(sym)
        if f is None:
            res.errors.append(f"{sym}: no filters, stop NOT placed")
            continue
        try:
            runner.c.cancel_all(sym)               # replace, never stack
            mark = runner.c.mark_price(sym)
            if units > 0:
                stop = quantize_price(mark * (1 - STOP_PCT), f.tick_size, "SELL")
                side = "SELL"
            else:
                stop = quantize_price(mark * (1 + STOP_PCT), f.tick_size, "BUY")
                side = "BUY"
            runner.c.place_order(
                symbol=sym, side=side, type="STOP_MARKET",
                stopPrice=f"{stop:f}".rstrip("0").rstrip("."),
                closePosition="true", workingType="MARK_PRICE",
                newClientOrderId=order_id("stop", sym, runner._clock))
            placed.append(sym)
        except Exception as e:
            if getattr(e, "code", None) == CODE_ORDER_TYPE_UNSUPPORTED:
                if not res.stops_unsupported:
                    res.stops_unsupported = True
                    log.warning(
                        "layer-1 stops UNAVAILABLE on this venue: conditional "
                        "orders refused on /fapi/v1/order (-4120). Layers 2 "
                        "(watchdog) and 3 (kill switch) remain armed.")
                continue
            res.errors.append(f"{sym}: stop not placed: {type(e).__name__}: {e}")
    return placed


def _record_funding(runner: "Phase2Runner", cl, res: CycleResult) -> dict:
    """STAGE10 4.3 + NOTES 46.2 criterion 2.

    Records each settlement against the position held AT THE SETTLEMENT
    INSTANT -- reconstructed from fill history, never read off the current
    book -- and reconciles the total against the exchange's own income
    history to $0.01.
    """
    now = int(runner._clock() * 1000)
    since = now - DAY_MS
    out: dict = {}
    try:
        funding_rows = runner.c.income("FUNDING_FEE", since, now)
        commission = runner.c.income("COMMISSION", since, now)
        realized = runner.c.income("REALIZED_PNL", since, now)
    except Exception as e:
        res.errors.append(f"income query failed: {type(e).__name__}: {e}")
        return {"error": str(e)}

    res.fees = -sum(float(r.get("income", 0.0)) for r in commission)
    res.price_pnl = sum(float(r.get("income", 0.0)) for r in realized)

    # fills of the last day, for the reconstruction
    fills: list[dict] = []
    for sym in {r.get("symbol") for r in funding_rows if r.get("symbol")}:
        try:
            for t in runner.c.user_trades(sym, start_ms=since):
                fills.append({"ts": int(t["time"]), "symbol": sym,
                              "side": "BUY" if t.get("buyer") else "SELL",
                              "qty": float(t["qty"])})
        except Exception as e:
            res.errors.append(f"{sym}: trade history unavailable: {e}")

    recorded = []
    for row in funding_rows:
        sym, ts = row.get("symbol"), int(row.get("time", 0))
        if not sym:
            continue
        units_then = reconstruct_position_at(fills, ts, sym)
        try:
            rates = runner.c.funding_rates(sym, ts - 60_000, ts + 60_000)
            rate = rates[-1][1] if rates else 0.0
            mark = runner.c.mark_price(sym)
        except Exception:
            rate, mark = 0.0, 0.0
        recorded.append(cl.record_funding(
            symbol=sym, position_units=units_then, mark=mark, rate=rate,
            actual_amount=float(row.get("income", 0.0)), ts_ms=ts,
            venue="testnet"))

    out = reconcile_funding(recorded, funding_rows)
    out["settlements"] = len(funding_rows)
    if not out["ok"]:
        res.errors.append(
            f"funding reconciliation drift {out['drift']:+.4f} exceeds "
            f"{out['tolerance']}")
    return out


# ---------------------------------------------------------------- the cycle

def _weights_from_units(units: dict, marks: dict, equity: float) -> dict:
    if not equity:
        return {}
    return {s: (u * marks.get(s, 0.0)) / equity for s, u in units.items()}


def run_cycle(runner: "Phase2Runner", costlog=None, execute: bool = True,
              log_path: Path | None = None) -> CycleResult:
    """One full paper cycle.

        fetch -> decide -> execute -> reconcile -> shadow -> report

    A SKIP is a legitimate outcome, not a failure: the frozen config skips
    21.55% of days on train at this size (NOTES 43.6) because the feasibility
    drop-loop cannot seat both legs. Nothing here may widen the book to avoid
    that -- NOTES 46.7 forbids tuning any strategy parameter on paper
    behaviour.
    """
    import json

    from live.costlog import CostLog

    cl = costlog if costlog is not None else CostLog()
    res = CycleResult(ts=runner._clock())
    cfg = runner.cfg

    short, excluded, guard = runner.candidate_symbols()
    res.universe = short
    res.excluded = guard["excluded"]
    res.guard_alert = guard["alert"]
    res.guard_reason = guard["reason"]

    state = reconcile.fetch_state(runner.c)
    res.equity = state.equity

    decision, snap = runner.decide(short, cfg.capital)
    if isinstance(decision, W.Skip):
        res.skip_reason = f"{decision.reason}: {decision.detail}"
        log.info("cycle SKIP %s", res.skip_reason)
    else:
        res.decision = dict(decision.final_weights)
        filters = runner.c.filters()
        units, problems = runner.target_units(decision, cfg.capital, filters)
        res.target_units = units
        res.errors.extend(problems)
        res.deltas = reconcile.plan_deltas(state.positions, units)

        if execute and res.deltas:
            for sym, delta in res.deltas.items():
                try:
                    side = "BUY" if delta > 0 else "SELL"
                    bid, ask = runner.c.book(sym)
                    intended = (bid + ask) / 2.0
                    cid = order_id("rb", sym, runner._clock)
                    # STAGE10 4.4: an ambiguous POST is queried by client id
                    # before any resubmit, never retried blind.
                    order = place_order_idempotent(
                        runner.c, symbol=sym, client_order_id=cid,
                        side=side, type="MARKET",
                        quantity=f"{abs(delta):f}".rstrip("0").rstrip("."))
                    if float(order.get("executedQty", 0) or 0) > 0:
                        for t in runner.c.user_trades(
                                sym, order_id=int(order["orderId"])):
                            # STAGE10 §6: venue tag from the FIRST fill, so
                            # testnet rows can never contaminate a real
                            # cost estimate later.
                            cl.record_fill(
                                symbol=sym, side=side, intended_price=intended,
                                fill_price=float(t["price"]), qty=float(t["qty"]),
                                fee=float(t["commission"]),
                                fee_asset=t["commissionAsset"],
                                maker=bool(t["maker"]),
                                order_type="MARKET", ts_ms=int(t["time"]),
                                order_id=int(t["orderId"]), trade_id=int(t["id"]),
                                venue="testnet")
                except FilterRejected as e:
                    res.errors.append(f"{sym}: filter rejected: {e}")
                except Exception as e:                      # pragma: no cover
                    res.errors.append(f"{sym}: {type(e).__name__}: {e}")

    after = reconcile.fetch_state(runner.c)
    res.filled = after.positions
    marks = {s: runner.c.mark_price(s) for s in after.positions}
    filled_w = _weights_from_units(after.positions, marks, cfg.capital)

    # ---- STAGE10 4.2: did a stop fire while we were away? -----------------
    stopped = detect_stop_fills(state.open_orders, state.positions,
                                after.positions)
    if stopped:
        res.stop_cascade = stopped
        log.warning("STOP CASCADE on %s -- reconciling and re-hedging", stopped)
        after = reconcile.fetch_state(runner.c)
        res.filled = after.positions
        marks = {s: runner.c.mark_price(s) for s in after.positions}
        filled_w = _weights_from_units(after.positions, marks, cfg.capital)

    # ---- STAGE10 4.1: is the FILLED book the book we intended? ------------
    if res.decision:
        betas = getattr(decision, "betas", None) or {}
        res.atomicity = check_atomicity(res.decision, filled_w, betas)
        if res.atomicity["needs_repair"]:
            log.error("ATOMICITY BREACH: %s", res.atomicity["detail"])
            res.errors.append(f"atomicity: {res.atomicity['detail']}")

    # ---- STAGE10 4.2 (layer 1): exchange-side reduce-only stops -----------
    if execute and after.positions:
        res.stops_placed = _place_stops(runner, after.positions, res)

    # ---- STAGE10 4.3 + criterion 2: funding, reconstructed and reconciled --
    res.funding = _record_funding(runner, cl, res)

    res.shadow = runner.shadow_check(short, cfg.capital, decision, filled_w)

    if log_path is not None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "kind": "phase2_cycle", "ts": res.ts,
                "universe_n": len(res.universe), "skip": res.skip_reason,
                "decision": res.decision, "deltas": res.deltas,
                "positions": res.filled, "shadow": res.shadow,
                "guard_alert": res.guard_alert, "guard_reason": res.guard_reason,
                "errors": res.errors, "equity": res.equity,
            }, default=str) + "\n")
    return res
