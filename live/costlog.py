"""
Cost-model ground truth: expected vs actual for every fill and every funding
settlement. This is the deliverable of paper trading that matters.

paper_costs.jsonl records (one JSON object per line):
  {"kind": "fill",    ts, symbol, side, intended_price, fill_price, qty,
                      notional, fee, fee_asset, maker, order_type,
                      slippage_bps}
  {"kind": "funding", ts, symbol, position_units, notional, rate,
                      expected_amount, actual_amount, sign_ok}

weekly_report() compares against the backtest's assumptions:
  taker 0.05%, maker 0.02%, slippage 0, funding = -units * mark * rate.

Never a headline PnL. There is no PnL field in any record on purpose.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from backtest.costs import FEE_RATES, funding_cashflow

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "paper_costs.jsonl"


def slippage_bps(side: str, intended: float, fill: float) -> float:
    """Signed cost in bps vs the intended (mid) price. Positive = paid more
    than intended (BUY filled above mid / SELL filled below mid)."""
    if intended <= 0:
        raise ValueError("intended price must be positive")
    raw = (fill - intended) / intended
    return 1e4 * (raw if side == "BUY" else -raw)


class CostLog:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def _append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def record_fill(self, *, symbol: str, side: str, intended_price: float,
                    fill_price: float, qty: float, fee: float, fee_asset: str,
                    maker: bool, order_type: str, ts_ms: int | None = None,
                    order_id: int | None = None, trade_id: int | None = None) -> dict:
        rec = {
            "kind": "fill",
            "ts": ts_ms or int(time.time() * 1000),
            "symbol": symbol, "side": side, "order_type": order_type,
            "intended_price": intended_price, "fill_price": fill_price,
            "qty": qty, "notional": qty * fill_price,
            "fee": fee, "fee_asset": fee_asset, "maker": bool(maker),
            "slippage_bps": slippage_bps(side, intended_price, fill_price),
            "order_id": order_id, "trade_id": trade_id,
        }
        self._append(rec)
        return rec

    def record_funding(self, *, symbol: str, position_units: float, mark: float,
                       rate: float, actual_amount: float, ts_ms: int) -> dict:
        expected = funding_cashflow(position_units, mark, rate)
        rec = {
            "kind": "funding", "ts": ts_ms, "symbol": symbol,
            "position_units": position_units, "notional": abs(position_units) * mark,
            "rate": rate, "expected_amount": expected, "actual_amount": actual_amount,
            # Sign is the thing the backtest could get backwards; check it
            # explicitly rather than only the magnitude.
            "sign_ok": (expected == 0 and actual_amount == 0)
                       or (expected * actual_amount > 0),
        }
        self._append(rec)
        return rec

    def records(self, since_ms: int = 0) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    if r.get("ts", 0) >= since_ms:
                        out.append(r)
        return out


def weekly_report(log: CostLog, since_ms: int) -> dict:
    """The B4 comparison. Returns the numbers and prints the block."""
    recs = log.records(since_ms)
    fills = [r for r in recs if r["kind"] == "fill"]
    fund = [r for r in recs if r["kind"] == "funding"]

    def fee_rate(sub: list[dict]) -> float | None:
        notional = sum(r["notional"] for r in sub)
        return (sum(r["fee"] for r in sub) / notional) if notional > 0 else None

    taker = fee_rate([r for r in fills if not r["maker"]])
    maker = fee_rate([r for r in fills if r["maker"]])
    slip = (sum(r["slippage_bps"] for r in fills) / len(fills)) if fills else None
    fund_mismatch = [r for r in fund if not r["sign_ok"]]
    fund_abs_err = (
        sum(abs(r["actual_amount"] - r["expected_amount"]) for r in fund) / len(fund)
        if fund else None
    )

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.3f}%"

    print("=== paper cost comparison (testnet: fills synthetic, fees/funding "
          "mechanics more trustworthy than fill quality) ===")
    print(f"assumed taker fee     {FEE_RATES['taker'] * 100:.3f}%   actual  {pct(taker)}"
          f"   ({len([r for r in fills if not r['maker']])} taker fills)")
    print(f"assumed maker fee     {FEE_RATES['maker'] * 100:.3f}%   actual  {pct(maker)}"
          f"   ({len([r for r in fills if r['maker']])} maker fills)")
    print(f"assumed slippage      0.000%   actual  "
          + ("n/a" if slip is None else f"{slip / 100:.3f}%")
          + "   <- the interesting one (indicative only on testnet)")
    print(f"funding: {len(fund)} settlements, {len(fund_mismatch)} sign mismatches, "
          f"mean |actual - expected| "
          + ("n/a" if fund_abs_err is None else f"{fund_abs_err:.6f} USDT"))
    return {
        "n_fills": len(fills), "taker_fee_actual": taker, "maker_fee_actual": maker,
        "slippage_bps_mean": slip, "n_funding": len(fund),
        "funding_sign_mismatches": len(fund_mismatch),
        "funding_mean_abs_err": fund_abs_err,
    }
