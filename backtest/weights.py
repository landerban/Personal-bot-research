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

# Beta REFERENCE only: its returns define the market factor. It need not be a
# position, and at small capital it is not tradeable at all (live MIN_NOTIONAL
# is $50 vs $5 for most alts) -- that is intentional and handled.
BTC = "BTCUSDT"
ANNUALISATION = 365.0  # perps trade every calendar day; 252 would understate

# Section 2.3.4 rank-weight band, as multiples of the leg average. The lower
# bound is also the smallest-position fraction the MIN_NOTIONAL universe
# filter assumes (pitdata.store.MIN_WEIGHT_FRACTION); Test 15 asserts the
# two agree. Vol scaling is applied separately and must not be folded in.
WEIGHT_BAND = (0.5, 1.5)

# Stage 2c 2.2: rescale-on-skip fires only when the held book's gross has
# drifted more than this from its target -- roughly half the drift needed
# to breach the floor. NOT a tunable parameter; do not grid it.
RESCALE_DEADBAND = 0.10

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
    lo, hi = WEIGHT_BAND[0] * avg, WEIGHT_BAND[1] * avg
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
    min_gross: float = 0.0,
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
    # Stage 2c 3: floor gross leverage so the smallest position clears
    # MIN_NOTIONAL at C=$100, N=10. Applied after the cap; Config
    # validation guarantees floor <= cap, so this cannot undo it.
    k = max(k, min_gross / gross)
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
        cfg.min_gross_leverage,
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


@dataclass(frozen=True)
class RescalePlan:
    """Stage 2c 2: what to do with a held book on a skip day."""
    alpha: float                  # scalar applied to every kept position's units
    pre_gross: float              # whole book at decision, in equity terms
    target_gross: float           # alpha * gross of the KEPT book
    dropped: tuple[str, ...]      # closed outright: under MIN_NOTIONAL after rescale
    est_vol_ann: float | None     # ex-ante vol of the kept book, None if unavailable
    mode: str                     # 'vol_target' | 'cap_floor_only'
    reason: str                   # the skip reason that triggered it


def plan_rescale(
    view: "PITView",
    cfg: "Config",
    positions: dict[str, float],
    marks: dict[str, float],
    equity: float,
    reason: str,
) -> RescalePlan | None:
    """
    On a skip, restore the vol target with ONE scalar on the held book.

    Deliberately does NOT re-rank: a skip means "I cannot re-rank today",
    so relative weights are preserved and only the scalar changes. This is
    available whatever the skip reason -- shrinking existing positions needs
    no viable universe -- which is why it works under universe_too_small,
    the most common reason.

    Subject to max_gross_leverage and the 2c 3 floor. A position the scalar
    would push under MIN_NOTIONAL is closed outright (reduce-only orders are
    floor-exempt on Binance) and the remainder re-planned. Inside the
    deadband, returns None: no trade, no fee.

    Reads only data available at view.as_of; the plan executes at the next
    open. If the held book's return window cannot be aligned with BTCUSDT's
    dates the vol estimate is unavailable, and the scalar comes from the cap
    and floor alone ('cap_floor_only') -- never from an imputed vol.
    """
    if not positions or equity <= 0:
        return None
    window = cfg.vol_window
    btc = _aligned_closes(view, BTC, window + 1)
    btc_times = btc[0] if btc is not None else None

    held = dict(positions)
    dropped: list[str] = []
    pre_gross = sum(abs(u) * marks[s] for s, u in held.items()) / equity

    for _ in range(len(positions) + 1):
        if not held:
            # everything under the floor: close the book outright
            return RescalePlan(0.0, pre_gross, 0.0, tuple(dropped), None,
                               "cap_floor_only", reason)
        syms = list(held)
        w = np.array([held[s] * marks[s] / equity for s in syms])
        gross = float(np.abs(w).sum())
        if gross <= 0:
            return None

        est: float | None = None
        if btc_times is not None:
            cols = []
            for s in syms:
                got = _aligned_closes(view, s, window + 1)
                if got is None or got[0] != btc_times or np.any(got[1] <= 0):
                    cols = None
                    break
                cols.append(np.diff(got[1]) / got[1][:-1])
            if cols is not None:
                R = np.column_stack(cols)
                cov = (np.cov(R.T, ddof=1) if len(syms) > 1
                       else np.array([[np.var(R[:, 0], ddof=1)]]))
                est = float(np.sqrt(max(float(w @ cov @ w), 0.0))
                            * np.sqrt(ANNUALISATION))

        alpha = (cfg.vol_target / est) if (est is not None and est > 0) else np.inf
        alpha = min(alpha, cfg.max_gross_leverage / gross)
        alpha = max(alpha, cfg.min_gross_leverage / gross)
        target = alpha * gross
        mode = "vol_target" if est is not None else "cap_floor_only"

        if abs(gross / target - 1.0) <= RESCALE_DEADBAND:
            # Within the deadband. If earlier iterations dropped positions
            # those still have to be closed, so emit a plan with alpha = 1.
            if not dropped:
                return None
            return RescalePlan(1.0, pre_gross, gross, tuple(dropped), est, mode, reason)

        under = [
            s for s in syms
            if (mn := view.min_notional(s)) is not None
            and alpha * abs(held[s]) * marks[s] < mn
        ]
        if not under:
            return RescalePlan(float(alpha), pre_gross, float(target),
                               tuple(dropped), est, mode, reason)
        dropped.extend(under)
        for s in under:
            del held[s]

    raise RuntimeError("rescale planning did not converge")  # unreachable
