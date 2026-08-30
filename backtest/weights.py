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
A symbol with a None signal, a missing bar inside the return window, a
window misaligned with BTCUSDT's dates, or no funding history yet (Stage
2d 5) is dropped from candidacy *before* ranking. Dropping after selection
would silently change the book.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

from backtest.universe_filter import filter_universe

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

# Stage 2d 5: a symbol is not a candidate until its funding history has
# begun. Binance's dumps start funding long after klines for some symbols
# (ICPUSDT +477 days, TLMUSDT +592, BNXUSDT +305); trading them in that
# window runs one leg cost-free and systematically understates costs on
# exactly the long-tail names momentum favours. Checking a trailing window
# rather than 'any funding ever' is cheap AND stricter: it also excludes a
# symbol during a mid-history funding gap, which has the same defect.
# Data policy, not strategy.
FUNDING_PRESENCE_WINDOW_MS = 3 * 86_400_000

# Stage 3c Part B: the window used to rank liquidity, matching the one
# `universe()` uses for its median-quote-volume test, so the cap and the
# filter measure the same thing.
LIQUIDITY_RANK_WINDOW = 30

# Stage 2e 1: feasibility is checked on post-hedge, post-vol-target weights.
# The universe filter estimates the smallest position as
# MIN_WEIGHT_FRACTION * L * C / N, but beta_hedge then scales the whole short
# leg by s (median 1.02, p95 1.73, max 4.03 on real data), so every short
# shrinks when s < 1 and the filter validated weights that no longer exist.
# An infeasible position is DROPPED and the remainder renormalised and
# re-hedged, at most this many times, then the rebalance is skipped.
MAX_FEASIBILITY_PASSES = 3

# Below this many names on either leg the book is a different strategy, so
# skip instead of trading it.
MIN_LEG_NAMES = 3

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
    beta_se_median: float             # 2e 5: beta estimation uncertainty
    beta_se_max: float
    beta_shrink_median: float         # |shrunk - raw|, median over the book
    # Stage 13 A.3: names the feasibility loop dropped for MIN_NOTIONAL before
    # this book was seated. Diagnostic only -- nothing reads it to decide
    # anything -- but "selected and dropped" cannot be reconstructed after the
    # fact, and the seating question needs it.
    dropped: tuple[str, ...] = ()
    # Stage 14: the SHRUNK betas the hedge actually executed on. The live
    # atomicity check (STAGE10 4.1) needs them to compute the residual beta of
    # the FILLED book; without them it silently scores every book 0.000 and
    # that half of the check never fires.
    betas: dict = field(default_factory=dict)


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


def compute_beta_ses(
    returns: np.ndarray, btc_returns: np.ndarray, betas: np.ndarray
) -> np.ndarray:
    """
    Standard error of each OLS beta: SE = sigma_resid / sqrt(Sxx), with
    sigma_resid^2 = RSS / (T - 2). Same slices as compute_betas.
    """
    b = btc_returns - btc_returns.mean()
    sxx = float(b @ b)
    if sxx <= 0:
        raise ValueError("BTC return variance is zero; beta SE undefined")
    t = returns.shape[0]
    if t <= 2:
        raise ValueError("need more than 2 observations for a beta SE")
    demeaned = returns - returns.mean(axis=0)
    resid = demeaned - np.outer(b, betas)
    rss = (resid ** 2).sum(axis=0)
    return np.sqrt(rss / (t - 2) / sxx)


def shrink_betas(betas: np.ndarray, ses: np.ndarray) -> np.ndarray:
    """
    Stage 2e 5: shrink each beta toward the market beta of 1.0 in proportion
    to its relative standard error, w = 1 / (1 + (SE/beta)^2), so
    beta_shrunk = w*beta + (1-w)*1.0.

    This is a RISK CONTROL, not an alpha choice: s > 3 on 1% of rebalances
    was estimation noise being executed as a hedge instruction. A beta
    estimated at ~0 has an infinite relative SE and shrinks all the way to
    1.0 -- that is the intended behaviour of the specified formula, and it
    is the conservative direction (assume market exposure when the estimate
    says nothing).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(betas != 0.0, ses / np.abs(betas), np.inf)
    w = 1.0 / (1.0 + rel ** 2)
    w = np.where(np.isfinite(w), w, 0.0)
    return w * betas + (1.0 - w) * 1.0


def leg_beta_se(weights: dict[str, float], betas: dict[str, float],
                ses: dict[str, float], positive: bool) -> tuple[float, float]:
    """(|weighted leg beta contribution|, SE of it) for one leg.

    The contribution is sum(|w_i| * beta_i); the SE combines independently as
    sqrt(sum (|w_i| * SE_i)^2). When the SE exceeds the contribution the hedge
    ratio is not identified and hedging on it executes noise (Stage 2e 5).

    Stage 17 I.4: this previously returned `(se, se)` -- it never computed the
    contribution its docstring promised, and had zero callers. Fixed to match
    the docstring AND wired into `build()` below, so there is one
    implementation rather than a correct open-coded one beside a wrong dead
    one. The refactor is proven inert by the §43.6 determinism check.
    """
    items = [(abs(w), betas[s], ses[s]) for s, w in weights.items()
             if (w > 0) == positive and w != 0]
    if not items:
        return 0.0, 0.0
    contrib = float(sum(w * b for w, b, _ in items))
    se = float(np.sqrt(sum((w * se_) ** 2 for w, _, se_ in items)))
    return abs(contrib), se


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


def select_legs(
    ordered: list[str],
    k: int,
    buffer: int = 0,
    held_sides: dict[str, int] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Section 2.3 leg selection, with the Stage 9 rank buffer (NOTES 45.1).

    `ordered` is the candidate list best-first by signal. Returns
    (longs best-first, shorts worst-first) -- the order `rank_weights`
    expects.

    `buffer` = 0, or nothing held, reproduces the frozen rule exactly: the
    top k and the bottom k. With a buffer, a HELD name is retained while it
    stays inside rank k+buffer of its own side, and only names that fall past
    that are replaced. Entry is unchanged: a new name enters at rank <= k.

    The three construction details are pre-registered in NOTES 45.1 and are
    not free here:
      1. retentions on BOTH legs resolve before either leg fills, so the rule
         is leg-symmetric and neither leg gets first claim on a contested name
      2. no name is used twice, and a name is only ever retained on the side
         it is actually held -- this is what keeps b=3 well defined where the
         two hold-zones touch (NOTES 45.2)
      3. leg order follows momentum rank, not hold status, so a retained name
         takes the weight its CURRENT rank earns. The buffer changes which
         names are held, never the weight profile.
    """
    if buffer <= 0 or not held_sides:
        return ordered[:k], ordered[-k:][::-1]

    m = len(ordered)
    rank = {s: i + 1 for i, s in enumerate(ordered)}
    long_zone_end = k + buffer            # retain a long while rank <= this
    short_zone_start = m - (k + buffer) + 1  # retain a short while rank >= this

    # 1. retentions first, both legs (rule 1). A name is only retained on the
    #    side it is held, so the two lists cannot collide (rule 2).
    ret_long = [s for s in ordered
                if held_sides.get(s, 0) > 0 and rank[s] <= long_zone_end][:k]
    ret_short = [s for s in reversed(ordered)
                 if held_sides.get(s, 0) < 0 and rank[s] >= short_zone_start][:k]
    used = set(ret_long) | set(ret_short)

    # 2. fill the vacancies from the best / worst unused ranks
    longs = list(ret_long)
    for s in ordered:
        if len(longs) >= k:
            break
        if s not in used:
            longs.append(s)
            used.add(s)
    shorts = list(ret_short)
    for s in reversed(ordered):
        if len(shorts) >= k:
            break
        if s not in used:
            shorts.append(s)
            used.add(s)

    # 3. order by current rank, not by hold status
    longs.sort(key=lambda s: rank[s])
    shorts.sort(key=lambda s: -rank[s])
    return longs, shorts


def compute_target_weights(
    view: "PITView",
    cfg: "Config",
    equity: float,
    signal_fn: SignalFn | None = None,
    gross_hint: float | None = None,
    held_sides: dict[str, int] | None = None,
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
    #
    # Stage 2e 1: the leverage passed here is the gross ACTUALLY in use --
    # the last realised gross, which is known at this close (same class of
    # path dependence as `capital=equity`). max_gross_leverage made the
    # filter a no-op: at C=$400, N=10 it admits anything under
    # 0.5 * 3.0 * 400/10 = $60, i.e. every symbol, while the book actually
    # runs near 0.45x where the threshold is $9. Re-ranking on a
    # feasibility-filtered pool would be substitution (2e 13), so this
    # stays a PRE-ranking eligibility rule, like the funding filter; the
    # exact post-hedge check below is the backstop.
    universe = view.tradeable_universe(
        capital=equity,
        gross_leverage=(gross_hint if gross_hint is not None
                        else cfg.max_gross_leverage),
        n_positions=n,
        min_quote_volume=cfg.min_quote_volume,
    )
    if len(universe) < n:
        return Skip("universe_too_small", f"{len(universe)} < {n}")

    # Stage 11 (NOTES 48.1): the universe rule is "top 15 by point-in-time
    # median quote volume, AMONG CRYPTO-ASSET PERPETUALS". §47 found the
    # volume-rank rule had drifted into tokenised equities, ETFs, commodities
    # and pre-market instruments while holding formally.
    #
    # Applied HERE -- before the liquidity ranking, beside the funding-presence
    # filter -- so the top-15 is the top-15 *among crypto*, not the crypto
    # subset of a mixed top-15. It is an eligibility rule, not a selection on
    # outcome.
    #
    # Unconditional, not a Config flag: the amendment REPLACED the rule rather
    # than adding an option, and a flag would let a future run silently opt out
    # of it. Proven inert on every day of train and validate (NOTES 48.5: 1,827
    # days, zero diffs) and pinned by Test 26, so no logged result moves.
    universe = filter_universe(universe)[0]
    if len(universe) < n:
        return Skip("universe_too_small",
                    f"{len(universe)} < {n} after the crypto-only filter")

    # Stage 3c Part B: drop the illiquid tail before ranking. Rank is
    # point-in-time by median quote volume over the trailing window --
    # the same measure `universe()` filters on. Applied BEFORE the
    # momentum ranking, so it is an eligibility rule like the funding
    # filter, not a selection on outcome.
    if cfg.max_liquidity_rank is not None:
        med = []
        for sym in universe:
            bars = view.klines(sym, "1d", limit=LIQUIDITY_RANK_WINDOW)
            if len(bars) < LIQUIDITY_RANK_WINDOW:
                continue
            med.append((median(b.quote_volume for b in bars), sym))
        med.sort(reverse=True)
        universe = sorted(s_ for _, s_ in med[:cfg.max_liquidity_rank])
        if len(universe) < n:
            return Skip("universe_too_small",
                        f"{len(universe)} < {n} after rank cap "
                        f"{cfg.max_liquidity_rank}")

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
    n_no_funding = 0
    for sym in universe:
        # Stage 2d 5: funding history must have begun (and be current).
        if not view.funding(sym, since=view.as_of - FUNDING_PRESENCE_WINDOW_MS):
            n_no_funding += 1
            continue
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
        return Skip(
            "insufficient_candidates",
            f"{len(candidates)} < {n}"
            + (f" ({n_no_funding} excluded: no funding history)" if n_no_funding else ""),
        )

    # 2.3 Rank descending; symbol name as tiebreak so ties (e.g. equal
    # signals) cannot make the book depend on dict ordering.
    candidates.sort(key=lambda c: (-c[1], c[0]))
    k = n // 2
    rets_by_sym = {c[0]: c[2] for c in candidates}
    # Stage 9: leg selection, with the rank buffer if one is configured.
    # rank_buffer=0 is the frozen rule and returns top-k / bottom-k.
    longs, shorts = select_legs(
        [c[0] for c in candidates], k, cfg.rank_buffer, held_sides
    )

    if not btc_var_ok:
        # 2.4 A zero-variance BTC makes every beta 0/0 - undefined, not zero.
        # Skip rather than default it; defaulting is imputation.
        return Skip("btc_zero_variance")

    def build(long_names: list[str], short_names: list[str]):
        """Sections 2.3-2.5 on a given selection: rank-weight, beta-hedge,
        vol-target. Returns (raw, hedged, final, beta_scale, vol_scale,
        est_vol, betas) or a Skip."""
        selected = long_names + short_names
        raw = rank_weights(long_names, short_names)
        ret_matrix = np.column_stack([rets_by_sym[s_] for s_ in selected])
        R = ret_matrix[-cfg.beta_window:]
        B = btc_rets[-cfg.beta_window:]
        raw_beta_arr = compute_betas(R, B)
        se_arr = compute_beta_ses(R, B, raw_beta_arr)
        # Stage 2e 5: execute a shrunk beta, not a noisy point estimate.
        beta_arr = shrink_betas(raw_beta_arr, se_arr)
        betas = dict(zip(selected, beta_arr.tolist()))
        ses = dict(zip(selected, se_arr.tolist()))

        # If a leg's weighted beta is smaller than its own standard error the
        # hedge ratio is not identified; hedging on it executes noise.
        for is_long in (True, False):
            contrib, se_leg = leg_beta_se(raw, betas, ses, is_long)
            if se_leg > contrib:
                return Skip(
                    "unhedgeable_beta",
                    f"{'long' if is_long else 'short'} leg beta "
                    f"{contrib:.3f} +/- {se_leg:.3f} (SE exceeds estimate)",
                )

        hedged = beta_hedge(raw, betas)
        if isinstance(hedged, Skip):
            return hedged
        hedged_w, beta_scale = hedged
        final_w, vol_scale, est_vol = vol_target_scale(
            hedged_w,
            selected,
            ret_matrix[-cfg.vol_window:],
            cfg.vol_target,
            cfg.max_gross_leverage,
            cfg.min_gross_leverage,
        )
        return (raw, hedged_w, final_w, beta_scale, vol_scale, est_vol,
                betas, ses, raw_beta_arr)

    # Stage 2e 1: check feasibility on the weights that will actually be
    # traded - after the hedge and the vol scale, not on the filter's
    # leg-average estimate. An infeasible position is dropped (never
    # substituted: substituting on feasibility selects for position size,
    # which correlates with vol and liquidity, and that tilt could not be
    # separated from the momentum signal afterwards), the remainder is
    # renormalised and re-hedged, and the check repeats.
    built = None
    dropped_syms: list[str] = []
    for _ in range(MAX_FEASIBILITY_PASSES):
        out = build(longs, shorts)
        if isinstance(out, Skip):
            return out
        (raw, hedged_w, final_w, beta_scale, vol_scale, est_vol,
         betas, ses, raw_betas) = out
        infeasible = [
            sym for sym, w in final_w.items()
            if (mn := view.min_notional(sym)) is not None
            and abs(w) * equity < mn
        ]
        if not infeasible:
            built = out
            break
        dropped_syms.extend(infeasible)
        longs = [x for x in longs if x not in infeasible]
        shorts = [x for x in shorts if x not in infeasible]
        if len(longs) < MIN_LEG_NAMES or len(shorts) < MIN_LEG_NAMES:
            return Skip(
                "below_min_notional_post_hedge",
                f"leg reduced to {len(longs)}L/{len(shorts)}S by "
                f"{','.join(sorted(infeasible))}",
            )
    if built is None:
        return Skip(
            "below_min_notional_post_hedge",
            f"still infeasible after {MAX_FEASIBILITY_PASSES} passes",
        )
    (raw, hedged_w, final_w, beta_scale, vol_scale, est_vol,
     betas, ses, raw_betas) = built

    min_pos = min(abs(w) * equity for w in final_w.values())
    binding: float | None = None
    for sym in final_w:
        mn = view.min_notional(sym)
        if mn is not None:
            binding = mn if binding is None else max(binding, mn)

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
        beta_se_median=float(np.median(list(ses.values()))),
        beta_se_max=float(max(ses.values())),
        beta_shrink_median=float(np.median(
            np.abs(np.array([betas[s_] for s_ in betas]) - raw_betas)
        )),
        dropped=tuple(dropped_syms),
        betas=dict(betas),
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
