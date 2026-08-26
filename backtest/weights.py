"""
Target-weight construction: universe -> signal -> rank-weight -> beta-hedge
-> vol-target, in exactly that order (spec section 2).

ORDER MATTERS
-------------
Rank-weight first, then beta-neutralise, then vol-target. The beta hedge
scales the whole short leg uniformly and the vol target scales the whole book
uniformly, so neither disturbs the [0.5x, 1.5x]-of-leg-average clip applied
at the rank-weight stage. Beta-neutralising last would.

DOLLAR NEUTRALITY VS BETA NEUTRALITY
------------------------------------
The rank-weight stage produces an exactly dollar-neutral book (legs sum to
+1 / -1; test 7 asserts on this stage). The beta hedge then scales the short
leg by s = beta_long / beta_short, which leaves a deliberate net dollar
position of (1 - s) per unit long gross whenever the legs' betas differ —
that net tilt *is* the hedge. Both cannot be exact simultaneously with a
single leg-scale; see NOTES.md. Test 5 checks the outcome (realised beta).

NO IMPUTATION
-------------
A symbol with a None signal, a missing bar inside the return window, or a
window misaligned with BTCUSDT's dates is dropped from candidacy *before*
ranking. Dropping after selection would silently change the book.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

if TYPE_CHECKING:  # avoid a runtime import cycle with engine.py
    from backtest.engine import Config
    from pitdata.store import PITView

BTC = "BTCUSDT"
ANNUALISATION = 365.0  # perps trade every calendar day; 252 would understate

# A leg-average beta below this is treated as exactly zero. Numerical guard
# against 0/0 on degenerate (e.g. flat-price) data, not a tunable parameter.
_BETA_EPS = 1e-12

SignalFn = Callable[["PITView", str, "Config"], Optional[float]]


def momentum_signal(view: "PITView", symbol: str, cfg: "Config") -> float | None:
    """The pre-registered signal: trailing return with a reversal skip."""
    return view.trailing_return(symbol, lookback=cfg.lookback, skip=cfg.skip)


@dataclass(frozen=True)
class Decision:
    """One rebalance decision, with each pipeline stage kept for the tests."""
    longs: tuple[str, ...]
    shorts: tuple[str, ...]
    raw_weights: dict[str, float]     # section 2.3 output: legs sum to +1/-1
    hedged_weights: dict[str, float]  # after short-leg beta scale
    final_weights: dict[str, float]   # after vol scale + gross cap
    beta_scale: float
    vol_scale: float
    est_vol_ann: float                # ex-ante, of the *hedged* (pre-scale) book
    gross: float                      # sum(|final_weights|)
    min_position_notional: float      # smallest |w| * equity in the book
    binding_min_notional: float | None  # largest MIN_NOTIONAL among positions


@dataclass(frozen=True)
class Skip:
    reason: str
    detail: str = ""


def rank_weights(longs: list[str], shorts: list[str]) -> dict[str, float]:
    """
    Rank-weight both legs: a linear ramp so extreme ranks get the largest
    magnitude, clipped to [0.5x, 1.5x] of the leg average, each leg
    normalised to 1.0 gross. Longs positive, shorts negative -> the output
    sums to zero exactly.

    `longs` ordered best-first; `shorts` ordered worst-first (so shorts[0]
    carries the largest short magnitude).
    """
    out: dict[str, float] = {}
    for sym, w in zip(longs, _leg_profile(len(longs))):
        out[sym] = w
    for sym, w in zip(shorts, _leg_profile(len(shorts))):
        out[sym] = -w
    return out


def _leg_profile(k: int) -> np.ndarray:
    """Descending leg weights: linear ramp, clipped, summing to exactly 1."""
    if k < 1:
        raise ValueError("empty leg")
    raw = np.arange(k, 0, -1, dtype=float)  # k, k-1, ..., 1
    w = raw / raw.sum()
    avg = 1.0 / k
    lo, hi = 0.5 * avg, 1.5 * avg
    # Clip-and-renormalise to a fixed point: renormalising after a clip can
    # push interior weights back outside the band, so iterate. The band
    # contains the average, so a fixed point exists; the ramp converges in a
    # few passes.
    for _ in range(100):
        clipped = np.clip(w, lo, hi)
        s = clipped.sum()
        if abs(s - 1.0) < 1e-15 and np.all(clipped >= lo - 1e-15) and np.all(
            clipped <= hi + 1e-15
        ):
            w = clipped
            break
        w = clipped / s
    else:
        raise RuntimeError("weight clipping failed to converge")
    # Loudly assert the §2.3.4 invariant rather than trusting the loop.
    assert abs(w.sum() - 1.0) < 1e-12
    assert np.all(w >= lo - 1e-12) and np.all(w <= hi + 1e-12)
    return w


def compute_betas(returns: np.ndarray, btc_returns: np.ndarray) -> np.ndarray:
    """
    OLS beta of each column of `returns` against `btc_returns`.

    returns: (T, N); btc_returns: (T,). Requires var(btc) > 0 — the caller
    must skip the rebalance on degenerate market data, not default it.
    """
    b = btc_returns - btc_returns.mean()
    var = float(b @ b)
    if var <= 0:
        raise ValueError("BTC return variance is zero; beta undefined")
    demeaned = returns - returns.mean(axis=0)
    return (b @ demeaned) / var


def beta_hedge(
    weights: dict[str, float], betas: dict[str, float]
) -> tuple[dict[str, float], float] | Skip:
    """
    Scale the short leg so portfolio beta ~= 0 (spec 2.4).

    s = beta_long_leg / beta_short_leg, with leg betas weighted by the leg's
    own weights. If both leg betas are ~0 the book is already beta-neutral
    (s = 1). If only the short leg's beta is ~0 or negative, no positive
    scale can hedge — skip the rebalance rather than fabricate one.
    """
    beta_long = sum(w * betas[s] for s, w in weights.items() if w > 0)
    beta_short = sum(-w * betas[s] for s, w in weights.items() if w < 0)
    if abs(beta_short) < _BETA_EPS:
        if abs(beta_long) < _BETA_EPS:
            return dict(weights), 1.0  # already beta-neutral
        return Skip("unhedgeable_beta", f"beta_long={beta_long:.4f}, beta_short~0")
    s = beta_long / beta_short
    if s <= 0:
        return Skip("unhedgeable_beta", f"scale={s:.4f} (sign flip)")
    hedged = {sym: (w if w > 0 else w * s) for sym, w in weights.items()}
    return hedged, s


def vol_target_scale(
    weights: dict[str, float],
    symbols: list[str],
    returns: np.ndarray,
    vol_target: float,
    max_gross: float,
) -> tuple[dict[str, float], float, float]:
    """
    Scale the book to `vol_target` annualised, hard-capped at `max_gross`.

    Estimator (the one implemented — see NOTES.md): ex-ante portfolio vol
    from the weighted sample covariance of constituent daily returns,
    sqrt(w' S w) * sqrt(365). The realised-portfolio-returns alternative was
    NOT tried; testing both would be an extra trial.

    Zero estimated vol (flat prices) makes the target unreachable; the scale
    is then the cap, which is what "scale to target, then cap" degenerates
    to. The cap exists because a beta-neutral book can run well under target
    and uncapped scaling would lever without bound.
    """
    w = np.array([weights[s] for s in symbols])
    cov = np.cov(returns.T, ddof=1)  # (N, N) sample covariance, daily
    var_daily = float(w @ cov @ w)
    est_vol_ann = float(np.sqrt(max(var_daily, 0.0)) * np.sqrt(ANNUALISATION))
    gross = float(np.abs(w).sum())
    if gross <= 0:
        raise ValueError("zero-gross book")
    cap_scale = max_gross / gross
    k = (vol_target / est_vol_ann) if est_vol_ann > 0 else np.inf
    k = min(k, cap_scale)
    return {s: wt * k for s, wt in weights.items()}, float(k), est_vol_ann


def _aligned_closes(view: "PITView", symbol: str, n_bars: int):
    """(open_times, closes) of the last n_bars daily bars, or None if short."""
    bars = view.klines(symbol, limit=n_bars)
    if len(bars) < n_bars:
        return None
    return [b.open_time for b in bars], np.array([b.close for b in bars])


def compute_target_weights(
    view: "PITView",
    cfg: "Config",
    equity: float,
    signal_fn: SignalFn | None = None,
) -> Decision | Skip:
    """
    The full section-2 pipeline at one `as_of`. Returns a Decision to be
    filled at the NEXT day's open, or a Skip with the reason. A Skip means
    hold the current book — never silently run a smaller or different one.
    """
    signal_fn = signal_fn or momentum_signal
    n = cfg.n_positions
    if n < 2 or n % 2:
        raise ValueError("n_positions must be even and >= 2")

    # 2.1 Universe at *current equity*: as the account compounds or draws
    # down, the set of symbols clearing MIN_NOTIONAL changes. Initial capital
    # here would be a subtle lookahead when equity has fallen.
    universe = view.tradeable_universe(
        capital=equity,
        gross_leverage=cfg.max_gross_leverage,
        n_positions=n,
        min_quote_volume=cfg.min_quote_volume,
    )
    if len(universe) < n:
        return Skip("universe_too_small", f"{len(universe)} < {n}")

    # Market proxy returns. BTC is the beta reference whether or not it is a
    # position; without its history there is no hedge, so skip.
    window = max(cfg.beta_window, cfg.vol_window)
    btc = _aligned_closes(view, BTC, window + 1)
    if btc is None:
        return Skip("btc_insufficient_history")
    btc_times, btc_closes = btc
    btc_rets = np.diff(btc_closes) / btc_closes[:-1]
    # Variance check on the exact slice the beta regression uses.
    btc_var_ok = float(np.var(btc_rets[-cfg.beta_window:])) > 0

    # 2.2 Signal; drop None. Also require a return window aligned with BTC's
    # dates — a gapped or misaligned window would need imputation to use.
    candidates: list[tuple[str, float, np.ndarray]] = []
    for sym in universe:
        sig = signal_fn(view, sym, cfg)
        if sig is None:
            continue
        got = _aligned_closes(view, sym, window + 1)
        if got is None or got[0] != btc_times:
            continue
        closes = got[1]
        if np.any(closes <= 0):
            continue
        candidates.append((sym, float(sig), np.diff(closes) / closes[:-1]))

    if len(candidates) < n:
        return Skip("insufficient_candidates", f"{len(candidates)} < {n}")

    # 2.3 Rank descending; symbol name as tiebreak so ties (e.g. equal
    # signals) cannot make the book depend on dict ordering.
    candidates.sort(key=lambda c: (-c[1], c[0]))
    k = n // 2
    longs = [c[0] for c in candidates[:k]]
    shorts = [c[0] for c in candidates[-k:]][::-1]  # worst first
    raw = rank_weights(longs, shorts)

    selected = longs + shorts
    rets_by_sym = {c[0]: c[2] for c in candidates}
    ret_matrix = np.column_stack([rets_by_sym[s] for s in selected])

    # 2.4 Beta-neutralise (60d window; the fetch window may be longer).
    # A zero-variance BTC makes every beta 0/0 — undefined, not zero. Skip
    # rather than default it; defaulting is imputation.
    if not btc_var_ok:
        return Skip("btc_zero_variance")
    beta_arr = compute_betas(
        ret_matrix[-cfg.beta_window:], btc_rets[-cfg.beta_window:]
    )
    betas = dict(zip(selected, beta_arr.tolist()))
    hedged = beta_hedge(raw, betas)
    if isinstance(hedged, Skip):
        return hedged
    hedged_w, beta_scale = hedged

    # 2.5 Vol-target, hard-capped at max gross leverage.
    final_w, vol_scale, est_vol = vol_target_scale(
        hedged_w,
        selected,
        ret_matrix[-cfg.vol_window:],
        cfg.vol_target,
        cfg.max_gross_leverage,
    )

    # Every executed position must clear its symbol's MIN_NOTIONAL. Position
    # notional at fill equals |weight| * equity by construction, so this is
    # checkable at decision time. Below the floor -> skip and log, never
    # silently drop or bump the position.
    min_pos = min(abs(w) * equity for w in final_w.values())
    binding: float | None = None
    for sym, w in final_w.items():
        mn = view.min_notional(sym)
        if mn is None:
            continue
        binding = mn if binding is None else max(binding, mn)
        if abs(w) * equity < mn:
            return Skip(
                "below_min_notional",
                f"{sym}: {abs(w) * equity:.2f} < {mn:.2f}",
            )

    return Decision(
        longs=tuple(longs),
        shorts=tuple(shorts),
        raw_weights=raw,
        hedged_weights=hedged_w,
        final_weights=final_w,
        beta_scale=beta_scale,
        vol_scale=vol_scale,
        est_vol_ann=est_vol,
        gross=float(sum(abs(w) for w in final_w.values())),
        min_position_notional=min_pos,
        binding_min_notional=binding,
    )
