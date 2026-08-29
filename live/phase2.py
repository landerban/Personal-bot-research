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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backtest import weights as W
from backtest.engine import Config
from backtest.universe_filter import filter_universe
from live import reconcile
from live.client import FilterRejected, TestnetClient
from live.pitfeed import LiveFeed

log = logging.getLogger("live.phase2")

ROOT = Path(__file__).resolve().parents[1]
DAY_MS = 86_400_000
BTC = W.BTC

# NOTES 49.4 limitation 2: pre-narrow before fetching klines. 40 against a
# top-15 rule. Not a strategy parameter -- a REST budget.
SHORTLIST = 40
# Shadow tolerance, STAGE10 §3.
WEIGHT_TOL = 1e-6


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


class Phase2Runner:
    """One paper cycle of the frozen config against testnet."""

    def __init__(self, client: TestnetClient, cfg: Phase2Config | None = None,
                 clock=time.time):
        self.c = client
        self.cfg = cfg or Phase2Config()
        self._clock = clock
        self.feed = LiveFeed(client, clock=clock)

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

        tickers = self.c.request("GET", "/fapi/v1/ticker/24hr")
        vol = {t["symbol"]: float(t.get("quoteVolume") or 0.0) for t in tickers}
        ranked = sorted(listed, key=lambda s: -vol.get(s, 0.0))

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
        guard = {
            "alert": alert, "reason": reason,
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
        view, snap = self.feed.build(symbols, as_of=as_of)
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
            view2, snap2 = shadow_feed.build(symbols)
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
                    order = runner.c.place_order(
                        symbol=sym, side=side, type="MARKET",
                        quantity=f"{abs(delta):f}".rstrip("0").rstrip("."),
                        newClientOrderId=cid)
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
