"""
The risk layer: paper equity, testnet-reset re-baselining, drawdown, kill switch.

WHY THIS EXISTS
---------------
Until now `status.json` carried `equity = capital` and `drawdown = 0.0` as
LITERALS. The dashboard displayed "kill switch armed" and "DD 0.0% of 30%" and
neither was measured. A status line that lies in the reassuring direction is
worse than no status line, so this module computes both for real.

PAPER EQUITY vs THE ACCOUNT
---------------------------
The testnet account holds ~5,000 USDT of play money; the paper book is sized
to $800. The two are not the same number and must not be conflated. The book's
equity is therefore tracked as

    paper_equity = capital + (exchange_balance - reference_balance)

where `reference_balance` is the account balance when the paper phase started.
Every position on the account is ours (reconcile refuses unknown positions), so
the account's *change* is the book's PnL.

TESTNET RESETS (NOTES 46.5)
---------------------------
Testnet wipes balances periodically. A reset must NEVER masquerade as a 100%
drawdown or a windfall, and must never fire the kill switch. So a balance move
that the day's own fills, fees and funding cannot explain is classified as a
reset: the reference is re-baselined, the paper equity series continues
unbroken, and the event is recorded. The kill switch keys off the re-baselined
series, exactly as pre-registered.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# An unexplained balance move larger than this is a reset, not PnL. Generous
# on purpose: testnet grants are thousands of USDT against an $800 book, so
# there is a wide gap between "a bad day" and "the balance was wiped", and
# misclassifying a real loss as a reset would disarm the kill switch.
RESET_ABS_USDT = 100.0
RESET_FRACTION_OF_CAPITAL = 0.25


@dataclass
class RiskState:
    """Persisted across cycles. The equity series is the re-baselined one."""
    capital: float = 800.0
    reference_balance: float | None = None      # account balance at phase start
    paper_equity: float | None = None
    peak_equity: float | None = None
    curve: list[list[float]] = field(default_factory=list)   # [[ts, equity]]
    resets: list[dict] = field(default_factory=list)
    cum_price_pnl: float = 0.0
    cum_funding_pnl: float = 0.0
    cum_fees: float = 0.0
    halted: bool = False
    halt_reason: str | None = None

    @classmethod
    def load(cls, path: Path | str) -> "RiskState":
        try:
            return cls(**json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    @property
    def drawdown(self) -> float:
        """Peak-to-current drawdown on the RE-BASELINED series. 0.0 before the
        series exists -- never None, so no caller has to special-case it."""
        if not self.peak_equity or self.paper_equity is None:
            return 0.0
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.paper_equity) / self.peak_equity)


def classify_balance_move(delta_balance: float, explained_pnl: float,
                          capital: float) -> tuple[bool, float]:
    """(is_reset, unexplained). A move the day's own fills/fees/funding cannot
    account for is a testnet reset, not trading PnL."""
    unexplained = delta_balance - explained_pnl
    threshold = max(RESET_ABS_USDT, RESET_FRACTION_OF_CAPITAL * capital)
    return abs(unexplained) > threshold, unexplained


def update(state: RiskState, ts: float, exchange_balance: float,
           price_pnl: float = 0.0, funding: float = 0.0, fees: float = 0.0,
           ) -> tuple[RiskState, dict | None]:
    """Fold one cycle into the risk state. Returns (state, reset_event|None).

    `price_pnl`, `funding` and `fees` are THIS cycle's realised amounts and are
    used only to decide whether a balance move is explainable. The equity
    series itself always comes from the account, so it cannot drift away from
    the exchange's own accounting.
    """
    reset_event = None

    if state.reference_balance is None:            # first cycle of the phase
        state.reference_balance = exchange_balance
        state.paper_equity = state.capital
        state.peak_equity = state.capital
        state.curve.append([ts, state.capital])
        return state, None

    prev_equity = state.paper_equity if state.paper_equity is not None else state.capital
    new_equity = state.capital + (exchange_balance - state.reference_balance)
    explained = price_pnl + funding - fees
    is_reset, unexplained = classify_balance_move(
        new_equity - prev_equity, explained, state.capital)

    if is_reset:
        # NOTES 46.5: re-baseline and carry the series forward unbroken. The
        # book's equity did not change -- the venue's bookkeeping did.
        state.reference_balance = exchange_balance - (prev_equity - state.capital)
        reset_event = {
            "ts": ts, "kind": "testnet_reset",
            "exchange_balance": exchange_balance,
            "unexplained": round(unexplained, 6),
            "paper_equity_held_at": round(prev_equity, 6),
            "note": "balance move unexplained by fills/fees/funding; series "
                    "re-baselined, kill switch NOT fired (NOTES 46.5)",
        }
        state.resets.append(reset_event)
        new_equity = prev_equity

    state.paper_equity = new_equity
    state.peak_equity = max(state.peak_equity or new_equity, new_equity)
    state.curve.append([ts, round(new_equity, 6)])
    state.curve = state.curve[-400:]               # bounded; the log has the rest
    state.cum_price_pnl += price_pnl
    state.cum_funding_pnl += funding
    state.cum_fees += fees
    return state, reset_event


def kill_switch_breached(state: RiskState, threshold: float = 0.30) -> bool:
    """True when the RE-BASELINED drawdown reaches the switch. A reset can
    never trip this, because a reset never moves paper_equity."""
    return state.drawdown >= threshold


def now_ms() -> int:
    return int(time.time() * 1000)
