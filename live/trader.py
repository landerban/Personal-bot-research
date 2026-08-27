#!/usr/bin/env python3
"""
Paper-trading main loop (TESTNET). Phase 1: deliberately trivial strategy.

  python live/trader.py --once                  # one rebalance now, then exit
  python live/trader.py                         # daily at 00:00 UTC, forever
  python live/trader.py --once --inject below-min-notional

WHAT THIS MEASURES (B0): the execution stack and the cost model. It does
NOT measure edge. There is no PnL in its output, on purpose.

PHASE 1 (B2): hold one long and one short in fixed liquid symbols, rebalance
daily at 00:00 UTC, sized exactly the way the backtester sizes a book —
through the SAME functions in backtest.weights (rank_weights, beta_hedge,
vol_target_scale) — at an equity cap of $100 so MIN_NOTIONAL and
quantisation are exercised at the real scale. The PnL of a fixed pair is
obviously uninterpretable, which is the point.

Phase 2 (the momentum config) is refused until the grid and holdout exist.

HARD RULES (B1): testnet only; exchange is truth (reconcile on start and
after every reconnect); fail closed (any unhandled exception flattens and
halts); keys from the environment, never logged.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import weights as W  # noqa: E402  -- the SAME module as the backtester
from live import reconcile  # noqa: E402
from live.client import (  # noqa: E402
    FilterRejected, SymbolFilters, TestnetClient, UserDataStream,
    quantize_price, quantize_qty,
)
from live.costlog import CostLog  # noqa: E402

log = logging.getLogger("live.trader")
ROOT = Path(__file__).resolve().parents[1]
DAY_MS = 86_400_000
BTC = W.BTC

INJECTIONS = ("below-min-notional", "unquantised", "clock-skew", "raise", "ws-kill")


@dataclass(frozen=True)
class PaperConfig:
    phase: int = 1
    long_symbol: str = "ETHUSDT"
    short_symbol: str = "BNBUSDT"
    equity_cap: float = 100.0          # size like the real $100 book
    fee_mode: str = "taker"            # "taker" -> MARKET; "maker" -> post-only LIMIT at touch
    vol_target: float = 0.20
    max_gross: float = 3.0
    min_gross: float = 1.05           # Stage 2c 3 floor, same as the backtester
    window: int = 60
    stop_pct: float = 0.20             # exchange-side reduce-only stop distance (safety, not strategy)
    maker_wait_s: float = 30.0
    heartbeat_s: float = 30.0
    settle_grace_s: float = 15.0       # trade slightly after 00:00 so funding has settled
    inject: str | None = None
    flatten_unknown: bool = False
    use_stream: bool = True

    @property
    def symbols(self) -> tuple[str, str]:
        return self.long_symbol, self.short_symbol


@dataclass
class DayStats:
    date: str
    rebalances_attempted: int = 0
    skips: dict[str, int] = field(default_factory=dict)
    fills: int = 0
    orders_rejected: int = 0
    maker_unfilled: int = 0
    total_fees: float = 0.0
    total_funding: float = 0.0
    reconnects: int = 0
    errors: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skips[reason] = self.skips.get(reason, 0) + 1


class HaltRequested(RuntimeError):
    pass


class Trader:
    def __init__(self, client: TestnetClient, cfg: PaperConfig,
                 costlog: CostLog | None = None,
                 paper_log: Path = ROOT / "paper_log.jsonl",
                 heartbeat: Path = ROOT / "live" / "state" / "heartbeat",
                 sleeper: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        if cfg.phase != 1:
            raise NotImplementedError(
                "Phase 2 (the momentum config) is refused until the grid and "
                "holdout are complete (STAGE2B B2). Phase 1 only.")
        if cfg.inject is not None and cfg.inject not in INJECTIONS:
            raise ValueError(f"unknown injection {cfg.inject!r}; one of {INJECTIONS}")
        self.c = client
        self.cfg = cfg
        self.costlog = costlog or CostLog()
        self.paper_log = Path(paper_log)
        self.heartbeat = Path(heartbeat)
        self._sleep = sleeper
        self._clock = clock
        self.stream: Optional[UserDataStream] = None
        self.stats = DayStats(self._today())
        self.halted = False

    # ------------------------------------------------------------ plumbing

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc).strftime("%Y-%m-%d")

    def beat(self) -> None:
        self.heartbeat.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat.write_text(f"{self._clock():.3f}", encoding="utf-8")

    def _sleep_with_heartbeat(self, seconds: float) -> None:
        end = self._clock() + seconds
        while True:
            self.beat()
            remaining = end - self._clock()
            if remaining <= 0:
                return
            self._sleep(min(self.cfg.heartbeat_s, remaining))

    def _log(self, rec: dict) -> None:
        rec = {"ts": int(self._clock() * 1000), **rec}
        self.paper_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.paper_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    # --------------------------------------------------------- reconcile

    def reconcile(self, why: str) -> reconcile.ExchangeState:
        """Exchange is truth. Unknown positions halt unless flatten_unknown."""
        state = reconcile.fetch_state(self.c)
        unknown = reconcile.unknown_positions(state, set(self.cfg.symbols))
        self._log({"kind": "reconcile", "why": why, "positions": state.positions,
                   "open_orders": len(state.open_orders), "equity": state.equity})
        if unknown:
            if not self.cfg.flatten_unknown:
                raise HaltRequested(f"unknown positions on account: {unknown}; "
                                    f"refusing to proceed (use --flatten-unknown)")
            for sym, units in unknown.items():
                self._close(sym, units, "flatten-unknown")
            state = reconcile.fetch_state(self.c)
        return state

    # ------------------------------------------------------------ sizing

    def _aligned_returns(self, symbols: list[str]) -> np.ndarray | None:
        """(window, len(symbols)) daily simple returns aligned on open_time."""
        series = {}
        ref_times = None
        for sym in symbols:
            bars = self.c.klines(sym, "1d", self.cfg.window + 1)
            if len(bars) < self.cfg.window + 1:
                return None
            times = [b[0] for b in bars]
            if ref_times is None:
                ref_times = times
            elif times != ref_times:
                return None
            closes = np.array([b[4] for b in bars])
            series[sym] = np.diff(closes) / closes[:-1]
        return np.column_stack([series[s] for s in symbols])

    def target_weights(self) -> dict[str, float] | W.Skip:
        """Phase 1 book through the backtester's own pipeline."""
        L, S = self.cfg.symbols
        raw = W.rank_weights([L], [S])                       # {L: +1, S: -1}
        R = self._aligned_returns([L, S, BTC])
        if R is None:
            return W.Skip("insufficient_history")
        betas = dict(zip([L, S], W.compute_betas(R[:, :2], R[:, 2]).tolist()))
        hedged = W.beta_hedge(raw, betas)
        if isinstance(hedged, W.Skip):
            return hedged
        hedged_w, _ = hedged
        final, k, est = W.vol_target_scale(hedged_w, [L, S], R[:, :2],
                                           self.cfg.vol_target, self.cfg.max_gross,
                                           self.cfg.min_gross)
        self._log({"kind": "weights", "final": final, "vol_scale": k,
                   "est_vol_ann": est, "betas": betas})
        return final

    def target_units(self, weights: dict[str, float], equity: float,
                     filters: dict[str, SymbolFilters]) -> dict[str, float] | W.Skip:
        eq = min(equity, self.cfg.equity_cap)
        out = {}
        for sym, w in weights.items():
            f = filters[sym]
            mid = self.c.mid(sym)
            dollars = abs(w) * eq
            qty = quantize_qty(dollars / mid, f.step_size)
            if self.cfg.inject == "unquantised":
                qty = Decimal(str(dollars / mid))            # deliberately raw
            notional = float(qty) * mid
            if self.cfg.inject != "below-min-notional" and (
                qty < f.min_qty or Decimal(str(notional)) < f.min_notional
            ):
                return W.Skip("below_min_notional",
                              f"{sym}: {notional:.2f} < {f.min_notional}")
            out[sym] = float(qty) * (1 if w > 0 else -1)
        return out

    # ------------------------------------------------------------- orders

    def _order_id(self, tag: str, sym: str) -> str:
        return f"p1-{tag}-{sym}-{int(self._clock())}"[:36]

    def _close(self, sym: str, units: float, tag: str) -> None:
        side = "SELL" if units > 0 else "BUY"
        self.c.place_order(symbol=sym, side=side, type="MARKET",
                           quantity=self._fmt(abs(units)), reduceOnly="true",
                           newClientOrderId=self._order_id(tag, sym))

    @staticmethod
    def _fmt(x: float | Decimal) -> str:
        s = f"{Decimal(str(x)):f}"
        return s.rstrip("0").rstrip(".") if "." in s else s

    def _execute(self, sym: str, delta: float, filters: SymbolFilters) -> None:
        side = "BUY" if delta > 0 else "SELL"
        qty = abs(delta) if self.cfg.inject == "unquantised" else float(quantize_qty(delta, filters.step_size))
        if self.cfg.inject == "below-min-notional":
            qty = float(filters.min_qty)  # smallest lot: notional will be under the floor
        bid, ask = self.c.book(sym)
        intended = (bid + ask) / 2.0
        cid = self._order_id("rb", sym)
        try:
            if self.cfg.fee_mode == "maker":
                price = quantize_price(bid if side == "BUY" else ask, filters.tick_size,
                                       "SELL" if side == "BUY" else "BUY")
                order = self.c.place_order(symbol=sym, side=side, type="LIMIT",
                                           timeInForce="GTX", price=self._fmt(price),
                                           quantity=self._fmt(qty), newClientOrderId=cid)
                deadline = self._clock() + self.cfg.maker_wait_s
                while order.get("status") not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                    if self._clock() >= deadline:
                        self.c.cancel_order(sym, cid)
                        self.stats.maker_unfilled += 1
                        self._log({"kind": "maker_unfilled", "symbol": sym, "qty": qty})
                        order = self.c.get_order(sym, cid)
                        break
                    self._sleep(2.0)
                    order = self.c.get_order(sym, cid)
            else:
                order = self.c.place_order(symbol=sym, side=side, type="MARKET",
                                           quantity=self._fmt(qty), newClientOrderId=cid)
        except FilterRejected as e:
            # Rejections are the point of injections 4/5: caught, logged, no crash.
            self.stats.orders_rejected += 1
            self.stats.skip("order_rejected")
            self._log({"kind": "order_rejected", "symbol": sym, "side": side,
                       "qty": qty, "code": e.code, "msg": str(e)})
            return
        self._record_fills(sym, side, intended, order)

    def _record_fills(self, sym: str, side: str, intended: float, order: dict) -> None:
        if float(order.get("executedQty", 0) or 0) <= 0:
            return
        for t in self.c.user_trades(sym, order_id=int(order["orderId"])):
            rec = self.costlog.record_fill(
                symbol=sym, side=side, intended_price=intended,
                fill_price=float(t["price"]), qty=float(t["qty"]),
                fee=float(t["commission"]), fee_asset=t["commissionAsset"],
                maker=bool(t["maker"]), order_type=order.get("type", "?"),
                ts_ms=int(t["time"]), order_id=int(t["orderId"]), trade_id=int(t["id"]),
            )
            self.stats.fills += 1
            self.stats.total_fees += float(t["commission"])
            self._log({"kind": "fill", **{k: rec[k] for k in
                       ("symbol", "side", "fill_price", "qty", "fee", "maker", "slippage_bps")}})

    def _place_stops(self, positions: dict[str, float], filters: dict[str, SymbolFilters]) -> None:
        """Layer-1 protection: exchange-side reduce-only stop per position,
        replaced daily. Survives this process, this machine, this ISP."""
        for sym, units in positions.items():
            self.c.cancel_all(sym)
            mark = self.c.mark_price(sym)
            if units > 0:
                stop = quantize_price(mark * (1 - self.cfg.stop_pct), filters[sym].tick_size, "SELL")
                side = "SELL"
            else:
                stop = quantize_price(mark * (1 + self.cfg.stop_pct), filters[sym].tick_size, "BUY")
                side = "BUY"
            self.c.place_order(symbol=sym, side=side, type="STOP_MARKET",
                               stopPrice=self._fmt(stop), closePosition="true",
                               workingType="MARK_PRICE",
                               newClientOrderId=self._order_id("stop", sym))
            self._log({"kind": "stop_placed", "symbol": sym, "side": side, "stop": float(stop)})

    # ---------------------------------------------------------- rebalance

    def rebalance_once(self) -> dict:
        self.stats.rebalances_attempted += 1
        if self.cfg.inject == "clock-skew":
            self.c.inject_clock_skew_ms = 5 * 60 * 1000   # +5 min: expect -1021 then resync
        if self.cfg.inject == "raise":
            raise RuntimeError("injected failure (B5 fail-closed check)")

        state = self.reconcile("rebalance")
        filters = self.c.filters()
        for sym in self.cfg.symbols:
            if sym not in filters:
                raise HaltRequested(f"{sym} has no tradeable filters on testnet")

        w = self.target_weights()
        if isinstance(w, W.Skip):
            self.stats.skip(w.reason)
            self._log({"kind": "skip", "reason": w.reason, "detail": w.detail})
            return {"skipped": w.reason}
        tgt = self.target_units(w, state.equity, filters)
        if isinstance(tgt, W.Skip):
            self.stats.skip(tgt.reason)
            self._log({"kind": "skip", "reason": tgt.reason, "detail": tgt.detail})
            return {"skipped": tgt.reason}

        deltas = reconcile.plan_deltas(state.positions, tgt)
        self._log({"kind": "plan", "current": state.positions, "target": tgt, "deltas": deltas})
        for sym, d in deltas.items():
            self._execute(sym, d, filters[sym])

        after = self.reconcile("post-fill")
        self._place_stops(after.positions, filters)
        self.beat()
        return {"target": tgt, "deltas": deltas, "positions": after.positions}

    # ------------------------------------------------------ daily record

    def record_day(self) -> dict:
        """Exchange-truth totals for the last 24h + the day's counters.
        No PnL field, by design."""
        now = int(self._clock() * 1000)
        fees = sum(float(r["income"]) for r in self.c.income("COMMISSION", now - DAY_MS, now))
        funding_items = self.c.income("FUNDING_FEE", now - DAY_MS, now)
        funding = 0.0
        positions = self.c.positions()
        for it in funding_items:
            amt = float(it["income"])
            funding += amt
            sym = it["symbol"]
            rates = self.c.funding_rates(sym, int(it["time"]) - 60_000, int(it["time"]) + 60_000)
            rate = rates[-1][1] if rates else 0.0
            self.costlog.record_funding(
                symbol=sym, position_units=positions.get(sym, 0.0),
                mark=self.c.mark_price(sym), rate=rate, actual_amount=amt,
                ts_ms=int(it["time"]),
            )
        rec = {
            "kind": "daily", "date": self.stats.date,
            "rebalances_attempted": self.stats.rebalances_attempted,
            "skips": self.stats.skips, "fills": self.stats.fills,
            "orders_rejected": self.stats.orders_rejected,
            "maker_unfilled": self.stats.maker_unfilled,
            "total_fees_exchange": -fees, "total_funding_exchange": funding,
            "reconnects": (self.stream.reconnects if self.stream else 0),
            "errors": self.stats.errors,
            "timestamp_resyncs": self.c.timestamp_resyncs,
            "backoffs": len(self.c.backoffs),
        }
        self._log(rec)
        self.stats = DayStats(self._today())
        return rec

    # ------------------------------------------------------------ safety

    def flatten_and_halt(self, reason: str) -> None:
        """Fail closed. Uses this process's client; the watchdog and kill
        switch are the independent layers if this itself is wedged."""
        self.halted = True
        self._log({"kind": "halt", "reason": reason})
        log.error("HALT: %s -- flattening", reason)
        try:
            for sym in set(self.cfg.symbols) | set(self.c.positions()):
                try:
                    self.c.cancel_all(sym)
                except Exception as e:  # keep flattening the rest
                    self._log({"kind": "halt_error", "step": f"cancel {sym}", "err": str(e)})
            for sym, units in self.c.positions().items():
                try:
                    self._close(sym, units, "halt")
                except Exception as e:
                    self._log({"kind": "halt_error", "step": f"close {sym}", "err": str(e)})
        finally:
            remaining = self.c.positions()
            self._log({"kind": "halted", "remaining": remaining})
            if remaining:
                log.error("NOT FLAT after halt: %s -- run live/killswitch.py", remaining)

    # --------------------------------------------------------------- run

    def _next_midnight_s(self) -> float:
        now = self._clock()
        day = 86_400
        return (int(now) // day + 1) * day + self.cfg.settle_grace_s

    def _start_stream(self) -> None:
        if not self.cfg.use_stream:
            return
        self.stream = UserDataStream(
            self.c,
            on_event=lambda ev: self._log({"kind": "ws", "e": ev.get("e"), "s": ev.get("o", {}).get("s")}),
            on_reconnect=lambda: (self.stats.__setattr__("reconnects", self.stats.reconnects + 1),
                                  self.reconcile("ws-reconnect")),
        )
        self.stream.start()

    def run(self, once: bool = False) -> int:
        try:
            self.c.sync_time()
            if self.c.dual_side_position():
                raise HaltRequested("account is in hedge mode; one-way mode required")
            self.reconcile("startup")
            self._start_stream()
            if self.cfg.inject == "ws-kill" and self.stream:
                self._sleep(3.0)
                self.stream.kill_connection()
            if once:
                out = self.rebalance_once()
                self.record_day()
                self._log({"kind": "once_done", "result": out})
                return 0
            while not self.halted:
                wait = self._next_midnight_s() - self._clock()
                log.info("next rebalance in %.0f s", wait)
                self._sleep_with_heartbeat(max(wait, 0))
                try:
                    self.rebalance_once()
                except (FilterRejected,) as e:
                    self.stats.errors.append(str(e))   # already logged; continue
                self.record_day()
            return 0
        except HaltRequested as e:
            self.flatten_and_halt(str(e))
            return 2
        except Exception as e:   # anything unhandled: flatten and halt, never continue blind
            self.stats.errors.append(f"{type(e).__name__}: {e}")
            log.exception("unhandled")
            self.flatten_and_halt(f"{type(e).__name__}: {e}")
            return 2
        finally:
            if self.stream:
                self.stream.stop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--long", default="ETHUSDT")
    p.add_argument("--short", default="BNBUSDT")
    p.add_argument("--equity-cap", type=float, default=100.0)
    p.add_argument("--fee-mode", choices=("taker", "maker"), default="taker")
    p.add_argument("--inject", choices=INJECTIONS, default=None)
    p.add_argument("--flatten-unknown", action="store_true")
    p.add_argument("--no-stream", action="store_true")
    p.add_argument("--phase", type=int, default=1)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = PaperConfig(phase=a.phase, long_symbol=a.long, short_symbol=a.short,
                      equity_cap=a.equity_cap, fee_mode=a.fee_mode, inject=a.inject,
                      flatten_unknown=a.flatten_unknown, use_stream=not a.no_stream)
    return Trader(TestnetClient(), cfg).run(once=a.once)


if __name__ == "__main__":
    sys.exit(main())
