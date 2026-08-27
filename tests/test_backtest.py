"""
Backtester tests. The null tests (1, 2, 3) matter most: they are not about
proving the strategy works, they are about proving the HARNESS does not
manufacture edge. If a random signal is profitable, the harness has a bug.

All tests run against synthetic stores built through the same
PointInTimeStore/PITView machinery as production — no mocking of Stage 1.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import costs, metrics  # noqa: E402
from backtest.engine import Config, run_backtest  # noqa: E402
from backtest.weights import (  # noqa: E402
    Skip,
    _leg_profile,
    beta_hedge,
    compute_target_weights,
    momentum_signal,
    rank_weights,
)
from pitdata.store import PointInTimeStore  # noqa: E402

DAY = 86_400_000
H8 = 8 * 3_600_000
# A real UTC midnight that is also a multiple of 8h, so synthetic funding
# stamps land exactly on the epoch-aligned settlement calendar.
T0 = 1_600_041_600_000  # 2020-09-14T00:00:00Z
assert T0 % H8 == 0

BTC = "BTCUSDT"
TAKER = 0.0005

CFG = Config(lookback=14, skip=2, initial_capital=10_000.0)

# Where the pre-fix module copies live for the "demonstrate the bug" tests.
SCRATCH = os.environ.get(
    "XSMOM_SCRATCH",
    r"C:\Users\ASUSTU~1\AppData\Local\Temp\claude\c--Stock"
    r"\be609a9b-c823-4528-87ff-52096ba7681a\scratchpad",
)


# ---------------------------------------------------------------- fixtures

def _minute_rows(daily_rows, drift: float = 0.0, n: int = 5):
    """The first `n` one-minute bars of each daily bar. Stage 2e 2: the
    engine fills at the +1min open, so a fixture without these has no
    tradeable execution bar and the whole fill path goes silently vacuous.
    With drift=0 every minute opens at the daily open; a nonzero drift makes
    minute m open at open*(1 + m*drift), which is how Test 22 proves the
    fill uses 00:01 rather than 00:00."""
    out = []
    for r in daily_rows:
        ot, op_ = r[0], r[2]
        for m in range(n):
            px = op_ * (1.0 + m * drift)
            out.append((ot + m * 60_000, ot + (m + 1) * 60_000 - 1,
                        px, px * 1.0005, px * 0.9995, px, 1.0, 2e5, 10))
    return out


def build_store(
    closes: dict[str, np.ndarray],
    opens: dict[str, np.ndarray] | None = None,
    quote_volumes: dict[str, float] | None = None,
    funding_rates: dict[str, float] | None = None,
    min_notionals: dict[str, float] | None = None,
    minute_drift: float = 0.0,
) -> PointInTimeStore:
    """Synthetic market. Default open_d = close_{d-1} (continuous prints).

    Also writes the first 5 one-minute bars of each day, which is what
    the engine actually fills against (Stage 2e 2).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    filters = []
    for sym, cl in closes.items():
        cl = np.asarray(cl, dtype=float)
        if opens is not None and sym in opens:
            op = np.asarray(opens[sym], dtype=float)
        else:
            op = np.concatenate([[cl[0]], cl[:-1]])
        qv = (quote_volumes or {}).get(sym, 2e7)
        rate = (funding_rates or {}).get(sym, 1e-4)
        rows, frows = [], []
        for d in range(len(cl)):
            ot = T0 + d * DAY
            hi = max(op[d], cl[d]) * 1.001
            lo = min(op[d], cl[d]) * 0.999
            rows.append((ot, ot + DAY - 1, op[d], hi, lo, cl[d], 10.0, qv, 100))
            for h in range(3):
                frows.append((ot + h * H8, rate))
        store.insert_klines(sym, "1d", rows)
        store.insert_klines(sym, "1m", _minute_rows(rows, minute_drift))
        store.insert_funding(sym, frows)
        mn = (min_notionals or {}).get(sym, 1.0)
        filters.append({"symbol": sym, "status": "TRADING", "min_notional": mn})
    store.insert_filters(T0, filters)
    return store


def factor_market(
    seed: int, n_days: int = 500, n_alts: int = 14,
    idio_vol: float = 0.03, btc_vol: float = 0.02, ar: float = 0.0,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Zero-drift one-factor market: r_i = beta_i * r_btc + eps_i."""
    rng = np.random.default_rng(seed)
    r_btc = rng.normal(0, btc_vol, n_days)
    closes = {BTC: 20_000 * np.cumprod(1 + r_btc)}
    betas = np.linspace(0.6, 1.4, n_alts)
    for i in range(n_alts):
        eps = rng.normal(0, idio_vol, n_days)
        if ar > 0:  # AR(1) idio: time-local momentum a shuffle destroys
            u = np.zeros(n_days)
            for t in range(1, n_days):
                u[t] = ar * u[t - 1] + eps[t]
            eps = u
        r = betas[i] * r_btc + eps
        closes[f"ALT{i:02d}USDT"] = 100 * np.cumprod(1 + r)
    return closes, r_btc


def run(store, cfg=CFG, n_days=500, signal_fn=None):
    return run_backtest(
        store, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1, signal_fn=signal_fn
    )


def day_of(ts_view: int) -> int:
    """Day index whose bar closes at this view stamp."""
    return (ts_view + 1 - T0) // DAY - 1


def gross_curve(res) -> np.ndarray:
    """Gross (pre-cost) equity over the strategy window."""
    idx = res.timestamps.index(res.rebalances[0].ts_fill)
    return np.array(res.gross_equity[max(idx - 1, 0):])


_SHARED: dict = {}


def shared_factor_run():
    """One momentum run on a plain factor market, reused by tests 4-8."""
    if "res" not in _SHARED:
        closes, r_btc = factor_market(seed=3)
        _SHARED["closes"], _SHARED["r_btc"] = closes, r_btc
        _SHARED["res"] = run(build_store(closes))
    return _SHARED["res"]


def index_signal_until(cutoff_view_ms: int):
    """Deterministic ranking (ALT00 best ... ALT13 worst) until `cutoff`,
    then None for everyone -> every later decision is a skip."""
    def sig(view, symbol, cfg):
        if view.as_of >= cutoff_view_ms or not symbol.startswith("ALT"):
            return None
        return -int(symbol[3:5])
    return sig


def breach_market(seed: int = 5, n_days: int = 400, cutoff: int = 300):
    """Factor market where, after `cutoff`, the deterministic longs
    (ALT00-04) collapse 10%/day and the shorts (ALT09-13) rally 4%/day for
    30 days. A book HELD through the skips loses most of its equity while
    its notional does not shrink -> leverage explodes. Returns (closes,
    cutoff_view_ms)."""
    closes, _ = factor_market(seed=seed, n_days=n_days)
    for sym in list(closes):
        if not sym.startswith("ALT"):
            continue
        i = int(sym[3:5])
        f = 0.90 if i <= 4 else (1.04 if i >= 9 else 1.0)
        c = closes[sym].copy()
        for d in range(cutoff + 1, min(cutoff + 31, n_days)):
            c[d] = c[d - 1] * f
        for d in range(cutoff + 31, n_days):
            c[d] = c[d - 1]
        closes[sym] = c
    return closes, T0 + cutoff * DAY - 1 + DAY


# ------------------------------------------------------- unit: weights math

def test_rank_weight_profile():
    """Linear ramp, clipped to [0.5x, 1.5x] of leg average, sums to 1."""
    w = _leg_profile(5)
    assert np.allclose(w, [0.3, 4 / 15, 0.2, 2 / 15, 0.1], atol=1e-12), w
    for k in (1, 2, 3, 5, 8, 25):
        w = _leg_profile(k)
        avg = 1.0 / k
        assert abs(w.sum() - 1.0) < 1e-12
        assert np.all(w >= 0.5 * avg - 1e-12)
        assert np.all(w <= 1.5 * avg + 1e-12)
        assert np.all(np.diff(w) <= 1e-15), "profile must be descending"
    rw = rank_weights(["A", "B"], ["C", "D"])
    assert abs(sum(rw.values())) < 1e-15
    assert rw["A"] > rw["B"] > 0 > rw["D"] > rw["C"]
    print("PASS rank_weight_profile")


def test_beta_hedge_math():
    w = {"A": 0.5, "B": 0.5, "C": -0.5, "D": -0.5}
    betas = {"A": 1.2, "B": 1.0, "C": 0.8, "D": 1.0}
    hedged, s = beta_hedge(w, betas)
    assert math.isclose(s, 1.1 / 0.9, rel_tol=1e-12)
    port_beta = sum(hedged[k] * betas[k] for k in hedged)
    assert abs(port_beta) < 1e-12, port_beta
    # long weights untouched, short leg scaled uniformly
    assert hedged["A"] == 0.5 and math.isclose(hedged["C"], -0.5 * s)
    # a short leg with negative beta cannot be hedged by positive scaling
    out = beta_hedge(w, {"A": 1.0, "B": 1.0, "C": -0.5, "D": -0.6})
    assert isinstance(out, Skip), "sign-flip beta must skip, not fabricate"
    print("PASS beta_hedge_math")


# --------------------------------------------------- unit: costs (test 9)

def test_funding_sign_unit():
    """Positive funding: longs PAY, shorts RECEIVE. The sign convention."""
    assert costs.funding_cashflow(2.0, 100.0, 1e-4) == -0.02   # long pays
    assert costs.funding_cashflow(-2.0, 100.0, 1e-4) == 0.02   # short receives
    assert costs.funding_cashflow(2.0, 100.0, -1e-4) == 0.02   # neg: long receives
    assert costs.funding_cashflow(-2.0, 100.0, -1e-4) == -0.02
    assert costs.trade_fee(-3.0, 10.0, "taker") == 3.0 * 10.0 * 0.0005
    assert costs.trade_fee(3.0, 10.0, "maker") == 3.0 * 10.0 * 0.0002
    try:
        costs.fee_rate("vip9")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown fee mode must raise")
    assert costs.settlement_times(T0 - 1, T0 + DAY - 1) == [
        T0, T0 + H8, T0 + 2 * H8
    ]
    assert costs.expected_settlement_count(T0 + DAY - 1, T0 + 2 * DAY - 1) == 3
    print("PASS funding_sign_unit")


# ----------------------------------------------------------- null test 3

def _flat_market(rates_pos: bool = True):
    """Flat alts; wiggling BTC excluded from the universe by low volume.

    Funding rates are chosen so BOTH legs pay (or both receive when
    inverted): with all-equal signals the tiebreak makes the book
    deterministic — longs are the alphabetically first 5, shorts the last 5.
    """
    n_days = 200
    closes = {BTC: 20_000 * (1 + 0.01 * (-1.0) ** np.arange(n_days))}
    rates = {}
    for i in range(14):
        sym = f"ALT{i:02d}USDT"
        closes[sym] = np.full(n_days, 100.0)
        if i <= 4:      # long leg under alphabetical tiebreak
            rates[sym] = 1e-3 if rates_pos else -1e-3
        elif i >= 9:    # short leg
            rates[sym] = -1e-3 if rates_pos else 1e-3
        else:
            rates[sym] = 0.0
    store = build_store(
        closes, quote_volumes={BTC: 1.0}, funding_rates=rates
    )
    return store, rates, n_days


def _replay_funding(res, rates: dict[str, float], mark: float) -> float:
    """
    Independent funding reconstruction, encoding the documented convention:
    00:00 settles the book held across midnight (pre-fill), 08:00/16:00 the
    post-fill book, all marked at the day's open.
    """
    pos: dict[str, float] = {}
    fills_by_ts = {rb.ts_fill: rb.fills for rb in res.rebalances}
    total = 0.0
    for t in res.timestamps:
        for sym, u in pos.items():
            total += -u * mark * rates[sym]          # 00:00, old book
        for sym, (du, _) in fills_by_ts.get(t, {}).items():
            pos[sym] = pos.get(sym, 0.0) + du
            if pos[sym] == 0.0:
                del pos[sym]
        for sym, u in pos.items():
            total += 2 * (-u * mark * rates[sym])    # 08:00 + 16:00, new book
    return total


def test_constant_price_zero_pnl_costs_exact():
    """Null test 3: flat prices -> gross PnL exactly 0; equity declines by
    exactly fees + funding, both reconciled independently."""
    store, rates, n_days = _flat_market()
    res = run(store, n_days=n_days)
    assert res.rebalances, "vacuous: nothing traded"
    assert res.gross_pnl == 0.0, f"flat prices produced PnL {res.gross_pnl}"
    assert res.missing_funding_settlements == 0

    expected_fees = res.total_turnover * TAKER
    assert math.isclose(res.total_fees, expected_fees, rel_tol=1e-12), (
        res.total_fees, expected_fees)

    expected_funding = _replay_funding(res, rates, mark=100.0)
    assert math.isclose(
        res.total_funding, expected_funding, rel_tol=1e-9, abs_tol=1e-9
    ), (res.total_funding, expected_funding)

    expected_equity = CFG.initial_capital - res.total_fees + res.total_funding
    assert math.isclose(res.equity[-1], expected_equity, rel_tol=1e-12), (
        res.equity[-1], expected_equity)
    assert res.equity[-1] < CFG.initial_capital, "costs must show up"
    print("PASS constant_price_zero_pnl_costs_exact")


# ---------------------------------------------- funding sign, engine level

def test_funding_sign_engine():
    """Null test 9, integration: with positive rates on the long leg and
    negative on the short leg, both legs pay -> total funding strictly
    negative; invert the rates -> strictly positive."""
    store, rates, n_days = _flat_market(rates_pos=True)
    res = run(store, n_days=n_days)
    assert res.rebalances
    assert res.total_funding < 0, "longs must PAY positive funding"
    assert math.isclose(
        res.total_funding, _replay_funding(res, rates, 100.0),
        rel_tol=1e-9, abs_tol=1e-9,
    )

    store2, rates2, _ = _flat_market(rates_pos=False)
    res2 = run(store2, n_days=n_days)
    assert res2.rebalances
    assert res2.total_funding > 0, "shorts must RECEIVE positive funding"
    print("PASS funding_sign_engine")


# ----------------------------------------------------------- null test 1
#
# STATISTICS OF THESE NULL TESTS
# One ~440-day run estimates an annualised Sharpe with SE ~ sqrt(365/440)
# ~ 0.9, so a single-seed "|SR| < 1" assertion false-alarms ~27% of the
# time. Each null test therefore averages N_SEEDS independent runs and
# requires the MEAN to sit within 2 standard errors of zero (Stage 2a: 30
# seeds, bound ~0.33), with a loose per-seed guard that still catches a
# gross mechanical leak on the first seed.

N_SEEDS = 30
SEED_BOUND = 3.0


def _null_stats(srs: list[float]) -> tuple[float, float, float, float, float]:
    """(mean, SE of mean, t-stat, min, max) across seeds."""
    a = np.array(srs)
    se = float(a.std(ddof=1) / math.sqrt(len(a)))
    return float(a.mean()), se, float(a.mean() / se), float(a.min()), float(a.max())


def _fmt_null(label: str, srs: list[float]) -> str:
    m, se, t, lo, hi = _null_stats(srs)
    return (f"{label}: mean {m:+.3f}  SE {se:.3f}  t {t:+.2f}  "
            f"min {lo:+.2f}  max {hi:+.2f}  (n={len(srs)})")


def test_random_signal_no_edge():
    """Null test 1: a seeded RNG signal must have ~zero gross Sharpe and a
    clearly negative net Sharpe. Profit here means the harness leaks."""
    gross_srs, net_srs = [], []
    for seed in range(N_SEEDS):
        closes, _ = factor_market(seed=seed)
        rng = np.random.default_rng(1000 + seed)

        def random_signal(view, symbol, cfg):
            return float(rng.random())

        res = run(build_store(closes), signal_fn=random_signal)
        assert len(res.rebalances) > 200, "vacuous: too few rebalances"
        gross_sr = metrics.sharpe(metrics.daily_returns(gross_curve(res)))
        _, eq = metrics.strategy_window(res)
        net_srs.append(metrics.sharpe(metrics.daily_returns(eq)))
        gross_srs.append(gross_sr)
        assert abs(gross_sr) < SEED_BOUND, (
            f"seed {seed}: gross sharpe {gross_sr:.2f} — HARNESS BUG")

    mean_gross, se, t, _, _ = _null_stats(gross_srs)
    mean_net = float(np.mean(net_srs))
    assert abs(mean_gross) <= 2 * se, (
        f"random signal shows systematic gross edge "
        f"({_fmt_null('gross', gross_srs)}) — HARNESS BUG, stop and investigate")
    assert mean_net < mean_gross, "costs must hurt"
    assert mean_net < -0.5, (
        f"random signal should lose clearly after costs, got {mean_net:+.2f}")
    print(f"PASS random_signal_no_edge | {_fmt_null('gross', gross_srs)} | "
          f"{_fmt_null('net', net_srs)}")


# ----------------------------------------------------------- null test 2

def test_shuffled_returns_no_edge():
    """Null test 2: shuffle each symbol's return series in time, keep the
    momentum signal. Any surviving edge is mechanical lookahead.

    Each series is DEMEANED before shuffling: a shuffle preserves the
    sample mean exactly, so per-symbol drift — cross-sectional, not
    temporal, structure — survives it, and momentum picks that drift up
    legitimately (verified: +0.43 mean gross Sharpe, t=2.7 over 30 seeds
    without demeaning; -0.06, t=-0.4 with). Demeaning isolates exactly the
    thing this test exists to catch: mechanical lookahead. See NOTES.md."""
    gross_srs = []
    for seed in range(N_SEEDS):
        closes, _ = factor_market(seed=seed, ar=0.25)  # time-local structure
        rng = np.random.default_rng(2000 + seed)
        shuffled = {}
        for sym, cl in closes.items():
            r = np.diff(cl) / cl[:-1]
            r = r - r.mean()
            rng.shuffle(r)
            shuffled[sym] = cl[0] * np.concatenate([[1.0], np.cumprod(1 + r)])

        res = run(build_store(shuffled))
        assert len(res.rebalances) > 50, "vacuous: too few rebalances"
        gross_sr = metrics.sharpe(metrics.daily_returns(gross_curve(res)))
        gross_srs.append(gross_sr)
        assert abs(gross_sr) < SEED_BOUND, (
            f"seed {seed}: gross sharpe {gross_sr:.2f} — lookahead")

    mean_gross, se, t, _, _ = _null_stats(gross_srs)
    assert abs(mean_gross) <= 2 * se, (
        f"edge survived time-shuffling ({_fmt_null('gross', gross_srs)}) — "
        f"lookahead in the harness")
    print(f"PASS shuffled_returns_no_edge | {_fmt_null('gross', gross_srs)}")


# ------------------------------------------------------------------ test 4

def test_fee_reconciliation():
    """Total fees == sum(turnover_notional x rate), two bookkeeping paths."""
    res = shared_factor_run()
    assert res.rebalances and not res.forced_liquidations
    turnover = sum(rb.turnover_notional for rb in res.rebalances)
    assert math.isclose(turnover, res.total_turnover, rel_tol=1e-12)
    assert math.isclose(res.total_fees, turnover * TAKER, rel_tol=1e-12)
    assert math.isclose(
        sum(rb.fees for rb in res.rebalances)
        + sum(rc.fees for rc in res.rescales),
        res.total_fees, rel_tol=1e-12
    )
    print("PASS fee_reconciliation")


# ------------------------------------------------------------------ test 5

def test_beta_neutrality():
    """Realised portfolio beta to BTC within +/-0.15 of zero."""
    res = shared_factor_run()
    ts, eq = metrics.strategy_window(res)
    rets = metrics.daily_returns(eq)
    btc_cl = _SHARED["closes"][BTC]
    days = [day_of(t) for t in ts]
    btc_rets = np.array([
        btc_cl[d] / btc_cl[d - 1] - 1 for d in days[1:]
    ])
    assert len(btc_rets) == len(rets)
    beta = float(np.cov(rets, btc_rets, ddof=1)[0, 1] / np.var(btc_rets, ddof=1))
    assert abs(beta) <= 0.15, f"realised beta {beta:.3f} out of band"
    print(f"PASS beta_neutrality (realised beta {beta:+.3f})")


# ------------------------------------------------------------------ test 6

def test_vol_targeting():
    """Realised annualised vol within +/-30% relative of the 20% target."""
    res = shared_factor_run()
    _, eq = metrics.strategy_window(res)
    vol = metrics.ann_vol(metrics.daily_returns(eq))
    assert 0.14 <= vol <= 0.26, f"realised vol {vol:.3f} outside [0.14, 0.26]"
    print(f"PASS vol_targeting (realised {vol:.3f})")


# ------------------------------------------------------------------ test 7

def test_dollar_neutrality():
    """The rank-weight construction sums to zero at every rebalance. (The
    beta hedge then deliberately tilts the net — see NOTES.md.)"""
    res = shared_factor_run()
    for rb in res.rebalances:
        s = sum(rb.raw_weights.values())
        assert abs(s) < 1e-9, f"raw weights sum {s} at {rb.ts_decision}"
    print("PASS dollar_neutrality")


# ------------------------------------------------------------------ test 8

def _leg_bounds_ok(weights: dict[str, float]) -> bool:
    for sign in (1, -1):
        leg = [abs(w) for w in weights.values() if w * sign > 0]
        if not leg:
            return False
        avg = sum(leg) / len(leg)
        if any(w < 0.5 * avg - 1e-9 or w > 1.5 * avg + 1e-9 for w in leg):
            return False
    return True


def test_weight_bounds_and_min_notional():
    res = shared_factor_run()
    for rb in res.rebalances:
        assert _leg_bounds_ok(rb.raw_weights), rb.ts_decision
        # leg scaling is uniform, so the band must survive to final weights
        assert _leg_bounds_ok(rb.final_weights), rb.ts_decision

    # Binding MIN_NOTIONAL: every executed position must clear the floor.
    closes, _ = factor_market(seed=4)
    mns = {sym: 4.0 for sym in closes}
    cfg = Config(lookback=14, skip=2, initial_capital=100.0)
    res2 = run(build_store(closes, min_notionals=mns), cfg=cfg)
    assert res2.rebalances, "vacuous: nothing traded at $100"
    for rb in res2.rebalances:
        for sym, w in rb.final_weights.items():
            notional = abs(w) * rb.equity_at_decision
            assert notional >= 4.0 - 1e-9, (
                f"{sym} position ${notional:.2f} below MIN_NOTIONAL at "
                f"{rb.ts_decision}")

    # Unclearable floor -> universe empty -> skip and log, never a smaller book.
    mns_high = {sym: 50.0 for sym in closes}
    res3 = run(build_store(closes, min_notionals=mns_high), cfg=cfg,
               n_days=100)
    assert not res3.rebalances
    assert res3.skips and all(r == "universe_too_small" for _, r, _ in res3.skips)
    print("PASS weight_bounds_and_min_notional")


# ----------------------------------------------------------------- test 10

def test_execution_timing():
    """Fills at the NEXT bar's open, never the signal bar's close. Overnight
    gaps make the two prices distinguishable."""
    closes, _ = factor_market(seed=5, n_days=300)
    opens = {}
    for sym, cl in closes.items():
        op = np.concatenate([[cl[0]], cl[:-1] * 1.02])  # 2% overnight gap
        opens[sym] = op
    res = run(build_store(closes, opens=opens), n_days=300)
    assert res.rebalances, "vacuous"
    for rb in res.rebalances:
        assert rb.ts_fill == rb.ts_decision + DAY
        d = day_of(rb.ts_fill)
        for sym, (_, price) in rb.fills.items():
            assert price == opens[sym][d], (
                f"fill at {price}, expected next-open {opens[sym][d]}")
            assert price != closes[sym][d - 1], (
                "filled at the signal bar's close — lookahead")
    print("PASS execution_timing")


# ----------------------------------------------------------------- test 12

def test_dollar_tilt_is_the_hedge():
    """Stage 2a ruling: the final book's net dollar exposure IS the beta
    hedge. With k the vol-target scale (long-leg gross) and s the short-leg
    scale, sum(final) = k(1 - s) = gross(1 - s)/(1 + s) exactly. Fails if
    anyone reintroduces exact dollar-neutrality on the final book."""
    res = shared_factor_run()
    tilts = []
    for rb in res.rebalances:
        s, k = rb.beta_scale, rb.vol_scale
        net = sum(rb.final_weights.values())
        assert abs(net - k * (1 - s)) < 1e-9, (net, k, s)
        assert abs(net - rb.gross * (1 - s) / (1 + s)) < 1e-9
        tilts.append(abs(net))
    assert max(tilts) > 1e-3, "tilt vanished — the hedge has been neutralised"
    print(f"PASS dollar_tilt_is_the_hedge (max |net| {max(tilts):.3f})")


# ----------------------------------------------------------------- test 13

def test_demeaned_db_is_faithful():
    """build_demeaned_db removes each symbol's mean log return (< 1e-10)
    and leaves volumes, funding, timestamps, filters and — the thing that
    matters — universe membership identical. Otherwise the drift
    decomposition would compare two different strategies."""
    from tools.build_demeaned_db import build_demeaned_db

    n_days = 300
    closes, _ = factor_market(seed=6, n_days=n_days)
    # inject explicit, heterogeneous drift so there is something to remove
    for i, sym in enumerate(list(closes)):
        closes[sym] = closes[sym] * np.exp(0.002 * (i - 7) * np.arange(n_days))
    src_store = build_store(closes)
    src_path = src_store.path
    src_store.close()
    dst_path = src_path + ".demeaned.db"

    mus = build_demeaned_db(src_path, dst_path)
    assert set(mus) == set(closes)
    try:
        build_demeaned_db(src_path, dst_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("must refuse to overwrite an existing output")

    a = PointInTimeStore(src_path, read_only=True)
    b = PointInTimeStore(dst_path, read_only=True)
    end = T0 + n_days * DAY - 1
    va, vb = a.view_as_of(end), b.view_as_of(end)
    for sym in closes:
        ba, bb = va.klines(sym, limit=10_000), vb.klines(sym, limit=10_000)
        assert len(ba) == len(bb) == n_days
        lr = np.diff(np.log([x.close for x in bb]))
        assert abs(lr.mean()) < 1e-10, (sym, lr.mean())
        lr_src = np.diff(np.log([x.close for x in ba]))
        assert abs(lr_src.mean()) > 1e-4 or sym == "ALT07USDT", "source untouched?"
        for x, y in zip(ba, bb):
            assert (x.open_time, x.close_time, x.volume, x.quote_volume,
                    x.trades) == (y.open_time, y.close_time, y.volume,
                                  y.quote_volume, y.trades)
            # intrabar structure preserved, only the drift removed
            assert math.isclose(x.close / x.open, y.close / y.open, rel_tol=1e-12)
        assert va.funding(sym) == vb.funding(sym)
        assert va.min_notional(sym) == vb.min_notional(sym)

    for d in (70, 120, 200, n_days - 1):
        a.reset_clock()
        b.reset_clock()
        t = T0 + (d + 1) * DAY - 1
        ua = a.view_as_of(t).tradeable_universe(100.0, 3.0, 10, 5e6)
        ub = b.view_as_of(t).tradeable_universe(100.0, 3.0, 10, 5e6)
        assert ua == ub and ua, f"universe differs at day {d}"
    a.close()
    b.close()
    print("PASS demeaned_db_is_faithful")


# ----------------------------------------------------------------- test 14

def test_realised_leverage_recorded():
    """Every filled rebalance reports a finite realised gross leverage,
    measured from the executed book, never above the cap, and matching
    the decided gross (a sizing bug between decision and fill would show
    up here as a mismatch)."""
    res = shared_factor_run()
    assert res.rebalances
    for rb in res.rebalances:
        L = rb.realised_gross_leverage
        assert math.isfinite(L) and L > 0
        assert L <= CFG.max_gross_leverage + 1e-9, L
        assert abs(L - rb.gross) < 1e-9, (L, rb.gross)
        assert rb.min_position_notional > 0
        assert rb.binding_min_notional == 1.0  # synthetic filter value
    # scheduled decisions = filled + skipped (each opportunity lands once)
    assert res.n_scheduled == len(res.rebalances) + len(res.skips)
    print("PASS realised_leverage_recorded")


# ----------------------------------------------------------------- test 15

def test_min_weight_fraction_single_source():
    """Stage 2b A2: the smallest-position fraction lives in ONE place. The
    universe filter default, the exported constant, the rank-weight band's
    lower bound and the diagnose tool must all be the same number."""
    import inspect

    from backtest import weights as w
    from pitdata import store as pit_store
    from tools import diagnose

    default = inspect.signature(
        pit_store.PITView.tradeable_universe
    ).parameters["min_weight_fraction"].default
    assert default == pit_store.MIN_WEIGHT_FRACTION == w.WEIGHT_BAND[0] == 0.5
    assert diagnose.MIN_WEIGHT_FRACTION is pit_store.MIN_WEIGHT_FRACTION
    # ...and the band the profile actually enforces is that band
    for k in (2, 5, 8):
        prof = w._leg_profile(k)
        assert prof.min() >= w.WEIGHT_BAND[0] / k - 1e-12
        assert prof.max() <= w.WEIGHT_BAND[1] / k + 1e-12
    print("PASS min_weight_fraction_single_source")


def test_grid_table_prints_from_summary():
    """The grid's closing comparison table must render from a real summary
    (it runs after six logged trials; a crash there is expensive to notice)."""
    from backtest import runner
    res = shared_factor_run()
    rows = [(CFG, runner.summarise(res)),
            (Config(lookback=14, skip=2, initial_capital=CFG.initial_capital,
                    min_quote_volume=CFG.min_quote_volume), runner.summarise(res))]
    runner.print_grid_table(rows, "train")
    print("PASS grid_table_prints_from_summary")


def test_pnl_trace_reconciles():
    """Per-symbol daily price PnL trace (postmortem input) must reconcile
    exactly with the engine's own totals: sum over days and symbols equals
    gross_pnl, and signed-side sums equal the long/short split."""
    res = shared_factor_run()
    assert len(res.pnl_by_symbol_day) == len(res.timestamps)
    total = sum(sum(d.values()) for d in res.pnl_by_symbol_day)
    assert abs(total - res.gross_pnl) < 1e-6, (total, res.gross_pnl)
    assert abs(res.gross_pnl_long + res.gross_pnl_short - res.gross_pnl) < 1e-6
    # every traded symbol appears; nothing is attributed to an unheld symbol
    held = set()
    for rb in res.rebalances:
        held |= set(rb.final_weights)
    traced = set()
    for d in res.pnl_by_symbol_day:
        traced |= set(d)
    assert traced <= held, traced - held
    print("PASS pnl_trace_reconciles")


# ----------------------------------------------------------------- test 16

def check_leverage_cap_every_day(res, cap: float) -> float:
    """The cap invariant over the DAILY trace (filled, skipped or held days).
    Returns the observed peak."""
    assert len(res.daily_leverage) == len(res.timestamps)
    peak = max(L for _, L in res.daily_leverage)
    for ts, L in res.daily_leverage:
        assert L <= cap + 1e-9, f"leverage {L:.2f}x on day {day_of(ts)} (cap {cap})"
    return peak


def test_leverage_cap_holds_every_day():
    """Stage 2c Test 16. The first real grid breached the 3x cap on days it
    did NOT trade, while the old assertion looked only at res.rebalances.
    Asserted here on every day of a skip-heavy fixture and of the plain
    factor run. Verified to FAIL against the pre-fix engine: peak 35.71x,
    8 days over cap, bankrupt (NOTES 14)."""
    closes, cutoff = breach_market()
    res = run(build_store(closes), n_days=400, signal_fn=index_signal_until(cutoff))
    assert any(r == "insufficient_candidates" for _, r, _ in res.skips), "fixture must skip"
    peak = check_leverage_cap_every_day(res, CFG.max_gross_leverage)
    peak2 = check_leverage_cap_every_day(shared_factor_run(), CFG.max_gross_leverage)
    print(f"PASS leverage_cap_holds_every_day (peak {peak:.2f}x skip-heavy, "
          f"{peak2:.2f}x plain)")


def test_below_min_notional_fires():
    """Stage 2c 1.3, updated by Stage 2e 1: the sizing-below-floor path must
    be live, not dead code, and no executed position may ever sit under its
    floor.

    The original premise (the filter assumes 3x, so hopeless symbols reach
    the sizing check) is exactly what 2e 1 removed: the universe filter now
    uses the gross ACTUALLY in use, so it honestly rejects symbols up front
    and most of these days never get as far as sizing. What remains
    reachable -- and what Test 21 exercises in depth -- is the post-hedge
    check, which catches positions the hedge and vol scale move under the
    floor after the filter has passed them."""
    floor = 6.0
    closes, _ = factor_market(seed=12)
    res = run(build_store(closes, min_notionals={s_: floor for s_ in closes}),
              cfg=Config(lookback=14, skip=2, initial_capital=100.0))
    reasons = res.skip_counts()

    # the floor path is reachable at all
    assert reasons.get("below_min_notional_post_hedge", 0) > 0, reasons
    # the pre-filter is doing its job at the realised gross, not at 3x
    assert reasons.get("universe_too_small", 0) > 0, reasons
    # ...and the invariant that actually matters: nothing traded under the floor
    n_pos = 0
    for rb in res.rebalances:
        for sym, w in rb.final_weights.items():
            assert abs(w) * rb.equity_at_decision >= floor - 1e-9, (sym, w)
            n_pos += 1
        assert rb.min_position_notional >= floor - 1e-9, rb.min_position_notional
    print(f"PASS below_min_notional_fires "
          f"({reasons['below_min_notional_post_hedge']} post-hedge skips, "
          f"{reasons['universe_too_small']} rejected up front, "
          f"{n_pos} executed positions all clearing ${floor:.0f})")


def test_rescale_on_skip():
    """Stage 2c Test 17. On a skip the held book keeps its proportions and a
    single scalar restores the vol target; inside the deadband nothing
    trades; a position the scalar pushes under MIN_NOTIONAL is closed and
    the remainder re-planned; consecutive skips keep leverage bounded."""
    from backtest.weights import RESCALE_DEADBAND, plan_rescale

    closes, cutoff = breach_market()
    res = run(build_store(closes), n_days=400, signal_fn=index_signal_until(cutoff))
    assert res.rescales, "the crash fixture must trigger at least one rescale"
    for rc in res.rescales:
        assert rc.ts_fill == rc.ts_decision + DAY
        kept = [s for s in rc.units_before if s not in rc.dropped]
        for sym in kept:  # ratios preserved: ONE scalar for everyone
            assert math.isclose(
                rc.units_after[sym] / rc.units_before[sym], rc.alpha, rel_tol=1e-12
            ), "rescale must not re-rank"
        for sym in rc.dropped:
            assert sym not in rc.units_after
        # open_d == close_{d-1} in this fixture, so realised == planned
        if not rc.dropped:
            assert abs(rc.post_gross - rc.target_gross) < 1e-9
        assert rc.post_gross <= CFG.max_gross_leverage + 1e-9
        # fired only outside the deadband
        assert abs(rc.pre_gross / rc.target_gross - 1.0) > RESCALE_DEADBAND
        assert abs(rc.fees - rc.turnover_notional * TAKER) < 1e-9
    check_leverage_cap_every_day(res, CFG.max_gross_leverage)
    # the first skip sees a book filled the day before: inside the deadband
    first_skip = min(ts for ts, r, _ in res.skips if r == "insufficient_candidates")
    assert not any(rc.ts_decision == first_skip for rc in res.rescales)
    assert abs(
        sum(rb.fees for rb in res.rebalances)
        + sum(rc.fees for rc in res.rescales) - res.total_fees
    ) < 1e-9

    # ---- unit level: deadband, ratios, drop rule, cap/floor-only ----
    class Bar:
        def __init__(self, ot, c):
            self.open_time, self.close = ot, c

    class FakeView:
        as_of = T0 + 100 * DAY - 1

        def __init__(self, series, floors, misaligned=()):
            self.series, self.floors, self.mis = series, floors, misaligned

        def klines(self, sym, limit=100):
            n = len(self.series[sym])
            shift = DAY if sym in self.mis else 0
            return [
                Bar(T0 + (n - limit + i) * DAY + shift, c)
                for i, c in enumerate(self.series[sym][-limit:])
            ]

        def min_notional(self, sym):
            return self.floors.get(sym)

    rng = np.random.default_rng(3)
    n = 100
    series = {BTC: 100 * np.cumprod(1 + rng.normal(0, 0.02, n))}
    for sym in ("A", "B", "C"):
        series[sym] = 100 * np.cumprod(1 + rng.normal(0, 0.03, n))
    cfg = Config(lookback=14, skip=2, initial_capital=100.0)
    floors = {"A": 5.0, "B": 5.0, "C": 5.0}
    view = FakeView(series, floors)
    marks = {sym: float(series[sym][-1]) for sym in ("A", "B", "C")}

    pos = {"A": 30 / marks["A"], "B": -30 / marks["B"], "C": 20 / marks["C"]}
    plan = plan_rescale(view, cfg, pos, marks, 100.0, "universe_too_small")
    assert plan is not None and plan.mode == "vol_target" and not plan.dropped
    assert abs(plan.target_gross - plan.alpha * 0.8) < 1e-12
    assert plan.target_gross >= cfg.min_gross_leverage - 1e-12
    assert plan.target_gross <= cfg.max_gross_leverage + 1e-12

    # already at target -> deadband -> no trade at all
    scaled = {sym: u * plan.alpha for sym, u in pos.items()}
    assert plan_rescale(view, cfg, scaled, marks, 100.0, "x") is None

    # a $4.50 leg that a shrink pushes under the $5 floor is dropped
    pos2 = {"A": 120 / marks["A"], "B": -120 / marks["B"], "C": 4.5 / marks["C"]}
    plan2 = plan_rescale(view, cfg, pos2, marks, 100.0, "x")
    assert plan2 is not None and plan2.alpha < 1.0 and plan2.dropped == ("C",)

    # misaligned history -> cap/floor only, never an imputed vol
    view_mis = FakeView(series, floors, misaligned=("B",))
    big = {"A": 200 / marks["A"], "B": -200 / marks["B"]}   # gross 4.0 > cap
    plan3 = plan_rescale(view_mis, cfg, big, marks, 100.0, "x")
    assert plan3 is not None and plan3.mode == "cap_floor_only"
    assert plan3.est_vol_ann is None
    assert abs(plan3.target_gross - cfg.max_gross_leverage) < 1e-12
    print(f"PASS rescale_on_skip ({len(res.rescales)} rescales, "
          f"{len(res.rescale_drops)} drops on the skip-heavy fixture)")


# ----------------------------------------------------------------- test 18

def test_leverage_floor_is_withdrawn_and_honoured_when_set():
    """Stage 2c Test 18, retargeted by Stage 2d 1.

    The default floor is 0.0 (WITHDRAWN) -- assert that, so it cannot creep
    back as a default. The mechanism still has to work when a floor IS set,
    since the field is kept, so the same assertion runs on an explicit floor."""
    assert CFG.min_gross_leverage == 0.0, "the floor is withdrawn (Stage 2d 1)"
    default_res = shared_factor_run()
    assert any(rb.realised_gross_leverage < 1.0 for rb in default_res.rebalances), (
        "with the floor withdrawn the book must be free to sit below 1.0x")

    floor = 1.05
    closes, _ = factor_market(seed=3)
    res = run(build_store(closes), cfg=Config(lookback=14, skip=2,
                                              min_gross_leverage=floor))
    n_bind = 0
    for rb in res.rebalances:
        assert rb.realised_gross_leverage >= floor - 1e-9
        n_bind += abs(rb.realised_gross_leverage - floor) < 1e-9
    for rc in res.rescales:
        if rc.units_after:
            assert rc.post_gross >= floor - 1e-9
    assert n_bind > 0, "fixture never reaches the floor; the test would be vacuous"
    print(f"PASS leverage_floor_is_withdrawn_and_honoured_when_set "
          f"(default 0.0; explicit floor binds on {n_bind} rebalances)")


# ----------------------------------------------------------------- test 20

def realistic_vol_market(seed: int = 21, n_days: int = 500):
    """A market whose HEDGED UNIT BOOK runs ~89% annualised vol -- the real
    median from the v1 replay (NOTES 13.1), not the ~21% of the plain
    synthetic fixture. Stage 2d 8: a fixture used to justify a risk
    parameter must match the volatility regime it will meet."""
    return factor_market(seed=seed, n_days=n_days, idio_vol=0.070, btc_vol=0.02)


def test_leverage_floor_fails_at_realistic_vol():
    """Stage 2d Test 20. The 1.05x floor was calibrated on a fixture whose
    unit-book vol is ~4x too low, where it never binds. At the REAL regime
    it binds on every rebalance and blows the risk budget. Pinning that as a
    reproducible failure, not an argument in a markdown file."""
    closes, _ = realistic_vol_market()
    measured = {}
    for floor in (0.0, 1.05):
        res = run(build_store(closes), n_days=500,
                  cfg=Config(lookback=14, skip=2, min_gross_leverage=floor))
        _, eq = metrics.strategy_window(res)
        L = np.array([rb.realised_gross_leverage for rb in res.rebalances])
        measured[floor] = {
            "vol": metrics.ann_vol(metrics.daily_returns(eq)),
            "dd": metrics.max_drawdown(eq),
            "gross_median": float(np.median(L)),
            "est": float(np.median([rb.est_vol_ann for rb in res.rebalances])),
            "peak": max(x for _, x in res.daily_leverage),
            "n": len(res.rebalances),
        }

    # The fixture itself must match the real regime, or the test proves nothing.
    est = measured[0.0]["est"]
    assert 0.75 <= est <= 1.05, f"fixture unit-book vol {est:.2f} is not the real regime"
    assert measured[0.0]["n"] > 200 and measured[1.05]["n"] > 200

    # 1. Floor withdrawn: the vol target is actually hit, gross sits near 0.45,
    #    and exposure does not grow without bound.
    off = measured[0.0]
    assert 0.14 <= off["vol"] <= 0.26, off["vol"]          # Test 6's band
    assert 0.30 <= off["gross_median"] <= 0.60, off["gross_median"]
    assert off["peak"] <= CFG.max_gross_leverage + 1e-9

    # 2. Floor at the withdrawn 1.05: realised vol blows past 40% and the
    #    drawdown passes the 30% kill switch. This is the failure mode.
    on = measured[1.05]
    assert on["vol"] > 0.40, f"floor failure not reproduced: vol {on['vol']:.3f}"
    assert on["dd"] > 0.30, on["dd"]
    assert abs(on["gross_median"] - 1.05) < 1e-9, "floor should bind on every rebalance"
    assert on["vol"] > 2.0 * off["vol"], (on["vol"], off["vol"])
    print(f"PASS leverage_floor_fails_at_realistic_vol "
          f"(unit-book vol {est:.2f}; floor off: vol {off['vol']:.3f} gross "
          f"{off['gross_median']:.2f} maxDD {off['dd']:.1%} | floor 1.05: vol "
          f"{on['vol']:.3f} gross {on['gross_median']:.2f} maxDD {on['dd']:.1%})")


def test_funding_start_exclusion():
    """Stage 2d 5: a symbol is not a candidate until its funding history has
    begun. Trading it earlier runs one leg cost-free and understates costs."""
    from backtest.weights import FUNDING_PRESENCE_WINDOW_MS

    n_days = 200
    closes, _ = factor_market(seed=31, n_days=n_days)
    late = "ALT03USDT"
    start_day = 150
    # Make the late symbol the strongest momentum name in the market, so it
    # MUST be picked whenever it is eligible. Then presence in the book is a
    # clean read on the funding filter alone.
    closes[late] = 100 * np.cumprod(np.full(n_days, 1.02))
    store = _store_with_late_funding(closes, late, start_day)
    cfg = Config(lookback=14, skip=2)

    store.reset_clock()
    v = store.view_as_of(T0 + 120 * DAY - 1)
    assert late in v.tradeable_universe(400.0, 3.0, 10, 5e6), "must be in the universe"
    assert not v.funding(late, since=v.as_of - FUNDING_PRESENCE_WINDOW_MS)
    dec = compute_target_weights(v, cfg, 400.0)
    assert not isinstance(dec, Skip), dec
    assert late not in dec.final_weights, "traded a symbol with no funding history"

    store.reset_clock()
    v2 = store.view_as_of(T0 + (start_day + 5) * DAY - 1)
    assert v2.funding(late, since=v2.as_of - FUNDING_PRESENCE_WINDOW_MS)
    dec2 = compute_target_weights(v2, cfg, 400.0)
    assert not isinstance(dec2, Skip), dec2
    assert late in dec2.final_weights, "still excluded after funding began"
    assert dec2.final_weights[late] > 0, "the strongest name must be a long"
    store.close()
    print(f"PASS funding_start_exclusion (top-signal symbol excluded before day "
          f"{start_day}, selected after)")


def _store_with_late_funding(closes, late_symbol: str, start_day: int):
    """build_store, but one symbol's funding only begins at `start_day`."""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    filters = []
    for sym, cl in closes.items():
        cl = np.asarray(cl, dtype=float)
        op = np.concatenate([[cl[0]], cl[:-1]])
        rows, frows = [], []
        for d in range(len(cl)):
            ot = T0 + d * DAY
            rows.append((ot, ot + DAY - 1, op[d], max(op[d], cl[d]) * 1.001,
                         min(op[d], cl[d]) * 0.999, cl[d], 10.0, 2e7, 100))
            if sym != late_symbol or d >= start_day:
                for h in range(3):
                    frows.append((ot + h * H8, 1e-4))
        store.insert_klines(sym, "1d", rows)
        store.insert_klines(sym, "1m", _minute_rows(rows, 0.0))
        if frows:
            store.insert_funding(sym, frows)
        filters.append({"symbol": sym, "status": "TRADING", "min_notional": 1.0})
    store.insert_filters(T0, filters)
    return store


def test_slippage_is_adverse_and_priced():
    """Stage 2c 4: slippage moves every fill against the trade, costs
    turnover x bps, and is reported. Zero bps must reproduce the old fills
    exactly (the sensitivity pair shares one code path)."""
    closes, _ = factor_market(seed=4)
    store_a, store_b = build_store(closes), build_store(closes)
    base = Config(lookback=14, skip=2, initial_capital=10_000.0)
    slipped = Config(lookback=14, skip=2, initial_capital=10_000.0,
                     slippage_bps_per_side=5.0)
    res0 = run(store_a, cfg=base)
    res5 = run(store_b, cfg=slipped)
    assert res0.total_slippage == 0.0
    assert res5.total_slippage > 0.0
    assert len(res0.rebalances) == len(res5.rebalances)
    rb0, rb5 = res0.rebalances[0], res5.rebalances[0]
    for sym, (delta, price) in rb5.fills.items():
        open_px = rb0.fills[sym][1]          # 0bps fill price IS the open
        assert price > open_px if delta > 0 else price < open_px
        assert math.isclose(abs(price / open_px - 1.0), 5e-4, rel_tol=1e-9)
    # cost equals turnover x bps, and it makes the strategy poorer
    assert math.isclose(res5.total_slippage,
                        sum(abs(d) * res0.rebalances[i].fills[s][1]
                            for i, rb in enumerate(res5.rebalances)
                            for s, (d, _) in rb.fills.items()) * 5e-4,
                        rel_tol=1e-6)
    assert res5.gross_pnl < res0.gross_pnl
    assert costs.slip_price(100.0, +1, 5.0) == 100.05
    assert costs.slip_price(100.0, -1, 5.0) == 99.95
    assert costs.slip_price(100.0, +1, 0.0) == 100.0
    assert costs.slippage_bps("ANY", None, slipped) == 5.0
    print(f"PASS slippage_is_adverse_and_priced (cost ${res5.total_slippage:.2f} "
          f"at 5bps vs ${res0.total_slippage:.2f} at 0)")


# ----------------------------------------------------------------- test 21

def hedge_skewed_market(seed: int = 41, n_days: int = 300):
    """Factor market where the SHORT leg (by the deterministic signal below)
    carries much higher beta than the long leg, so beta_hedge's
    s = beta_long / beta_short comes out far below 1 and every short shrinks
    after hedging. That is the case the universe filter's leg-average
    estimate cannot see: it validates 0.5 * L * C / N and the hedge then
    moves the actual positions somewhere else entirely."""
    rng = np.random.default_rng(seed)
    r_btc = rng.normal(0, 0.02, n_days)
    closes = {BTC: 20_000 * np.cumprod(1 + r_btc)}
    for i in range(14):
        beta = 0.3 if i <= 4 else (3.0 if i >= 9 else 1.0)
        closes[f"ALT{i:02d}USDT"] = 100 * np.cumprod(
            1 + beta * r_btc + rng.normal(0, 0.03, n_days)
        )
    return closes


def index_signal(view, symbol, cfg):
    """ALT00 best ... ALT13 worst, independent of prices, so the legs (and
    therefore the leg betas) are fixed by construction."""
    return None if not symbol.startswith("ALT") else -float(symbol[3:5])


def test_post_hedge_feasibility():
    """Stage 2e Test 21. Feasibility must be judged on the weights actually
    traded -- after the hedge and the vol scale. An infeasible position is
    DROPPED and the remainder renormalised and re-hedged; never substituted."""
    from backtest.weights import MIN_LEG_NAMES

    closes = hedge_skewed_market()
    cfg = Config(lookback=14, skip=2, initial_capital=400.0)
    eq = 400.0

    def decide(floor):
        store = build_store(closes, min_notionals={s_: floor for s_ in closes})
        store.reset_clock()
        view = store.view_as_of(T0 + 200 * DAY - 1)
        pool = view.tradeable_universe(eq, 3.0, 10, 5e6)
        out = compute_target_weights(view, cfg, eq, signal_fn=index_signal)
        store.close()
        return out, pool

    # Unconstrained: the fixture really does shrink the shorts ~13x, well
    # below the filter's 0.5 * 3.0 * 400/10 = $60 estimate.
    free, _ = decide(1.0)
    assert not isinstance(free, Skip), free
    assert free.beta_scale < 0.3, f"fixture must shrink shorts (s={free.beta_scale})"
    smallest = min(abs(w) * eq for w in free.final_weights.values())
    assert smallest < 8.0, smallest

    # An $8 floor cuts the two smallest shorts. Drop-and-renormalise must
    # rescue the rebalance rather than skipping it wholesale.
    dec, pool = decide(8.0)
    assert not isinstance(dec, Skip), f"drop-and-renormalise did not rescue: {dec}"
    for sym, w in dec.final_weights.items():
        assert abs(w) * eq >= 8.0 - 1e-9, (sym, abs(w) * eq)
    assert len(dec.longs) >= MIN_LEG_NAMES and len(dec.shorts) >= MIN_LEG_NAMES
    assert len(dec.final_weights) < len(free.final_weights), "nothing was dropped"
    # NO SUBSTITUTION: the book is a subset of the originally ranked names.
    assert set(dec.final_weights) <= set(free.final_weights), (
        set(dec.final_weights) - set(free.final_weights))
    assert set(dec.final_weights) <= set(pool)
    # neutrality preserved on the surviving book
    assert abs(sum(dec.raw_weights.values())) < 1e-9
    assert len(dec.raw_weights) == len(dec.longs) + len(dec.shorts)
    # An impossible floor: skip with the new reason, never a 2-name leg.
    dec2, _ = decide(500.0)
    assert isinstance(dec2, Skip), dec2
    assert dec2.reason in ("below_min_notional_post_hedge", "universe_too_small"), dec2

    # A floor that would leave a 2-name short leg: skip, never trade a
    # 2-name leg (that is a different strategy).
    dec3, _ = decide(10.0)
    assert isinstance(dec3, Skip), dec3
    assert dec3.reason == "below_min_notional_post_hedge", dec3
    assert "2S" in dec3.detail, dec3.detail
    print(f"PASS post_hedge_feasibility (s={free.beta_scale:.3f}; smallest "
          f"${smallest:.2f} at no floor -> {len(dec.longs)}L/{len(dec.shorts)}S "
          f"all clearing $8; a $10 floor would leave 2S so it skips)")


def test_post_hedge_feasibility_fails_pre_fix():
    """Stage 2e 12: Test 21 must fail against the pre-fix path. The old code
    checked the floor on final weights and skipped the WHOLE rebalance
    rather than dropping the offending position."""
    import importlib.util

    pre_path = Path(SCRATCH) / "weights_pre2e.py"
    if not pre_path.exists():
        print("SKIP post_hedge_feasibility_fails_pre_fix (pre-fix copy absent)")
        return
    spec = importlib.util.spec_from_file_location("weights_pre2e", pre_path)
    W = importlib.util.module_from_spec(spec)
    sys.modules["weights_pre2e"] = W
    spec.loader.exec_module(W)

    closes = hedge_skewed_market()
    cfg = Config(lookback=14, skip=2, initial_capital=400.0)
    store = build_store(closes, min_notionals={s_: 8.0 for s_ in closes})
    store.reset_clock()
    pre = W.compute_target_weights(store.view_as_of(T0 + 200 * DAY - 1), cfg,
                                   400.0, signal_fn=index_signal)
    store.close()
    assert isinstance(pre, W.Skip), (
        "pre-fix path did not skip; the test cannot demonstrate the bug")
    assert pre.reason == "below_min_notional", pre
    print(f"PASS post_hedge_feasibility_fails_pre_fix "
          f"(pre-fix skipped the whole rebalance: {pre.detail})")


# ----------------------------------------------------------------- test 24

def test_beta_shrinkage():
    """Stage 2e Test 24. A noisy beta shrinks toward 1.0; a cleanly measured
    one barely moves; and the hedge ratio s stays bounded under the noise
    that previously produced s > 3."""
    from backtest.weights import (
        compute_beta_ses, compute_betas, shrink_betas, beta_hedge,
    )

    rng = np.random.default_rng(77)
    T = 60
    btc = rng.normal(0, 0.02, T)

    # clean: beta 1.5, almost no residual -> tiny SE -> barely shrunk
    clean = np.column_stack([1.5 * btc + rng.normal(0, 0.0005, T)])
    b_clean = compute_betas(clean, btc)
    se_clean = compute_beta_ses(clean, btc, b_clean)
    sh_clean = shrink_betas(b_clean, se_clean)
    assert se_clean[0] / abs(b_clean[0]) < 0.05, se_clean
    assert abs(sh_clean[0] - b_clean[0]) < 0.02 * abs(b_clean[0]), (sh_clean, b_clean)

    # noisy: beta 0.2 buried in residual -> big relative SE -> pulled to 1.0
    noisy = np.column_stack([0.2 * btc + rng.normal(0, 0.20, T)])
    b_noisy = compute_betas(noisy, btc)
    se_noisy = compute_beta_ses(noisy, btc, b_noisy)
    sh_noisy = shrink_betas(b_noisy, se_noisy)
    assert se_noisy[0] > abs(b_noisy[0]), "fixture is not actually noisy"
    assert abs(sh_noisy[0] - 1.0) < abs(b_noisy[0] - 1.0), (sh_noisy, b_noisy)
    assert 0.5 < sh_noisy[0] < 1.5, sh_noisy

    # a beta estimated at ~0 has infinite relative SE -> goes to 1.0 exactly
    assert math.isclose(shrink_betas(np.array([0.0]), np.array([0.1]))[0], 1.0)

    # s is bounded under noise that previously produced s > 3: a tiny, noisy
    # short-leg beta is what made s explode.
    raw_w = {"L1": 0.5, "L2": 0.5, "S1": -0.5, "S2": -0.5}
    betas_hat = np.array([1.2, 1.1, 0.05, 0.08])       # shorts near zero
    ses = np.array([0.02, 0.02, 0.30, 0.30])           # and badly measured
    unshrunk = dict(zip(raw_w, betas_hat.tolist()))
    hedged_raw = beta_hedge(raw_w, unshrunk)
    assert not isinstance(hedged_raw, Skip)
    s_raw = hedged_raw[1]
    assert s_raw > 3.0, f"fixture must reproduce the old blow-up (s={s_raw})"
    shrunk = dict(zip(raw_w, shrink_betas(betas_hat, ses).tolist()))
    hedged_sh = beta_hedge(raw_w, shrunk)
    assert not isinstance(hedged_sh, Skip)
    s_shrunk = hedged_sh[1]
    assert s_shrunk < 2.0, f"shrinkage did not bound s (s={s_shrunk})"
    print(f"PASS beta_shrinkage (clean {b_clean[0]:.3f}->{sh_clean[0]:.3f}, "
          f"noisy {b_noisy[0]:.3f}->{sh_noisy[0]:.3f}, s {s_raw:.1f}->{s_shrunk:.2f})")


def test_unhedgeable_when_se_exceeds_estimate():
    """Stage 2e 5: skip when a leg's weighted beta SE exceeds its estimate --
    the hedge ratio is not identified and hedging on it executes noise."""
    n_days = 300
    rng = np.random.default_rng(9)
    r_btc = rng.normal(0, 0.02, n_days)
    closes = {BTC: 20_000 * np.cumprod(1 + r_btc)}
    for i in range(14):
        # essentially zero beta, huge idiosyncratic noise: SE >> |beta|
        closes[f"ALT{i:02d}USDT"] = 100 * np.cumprod(
            1 + 0.0 * r_btc + rng.normal(0, 0.25, n_days))
    store = build_store(closes)
    store.reset_clock()
    dec = compute_target_weights(store.view_as_of(T0 + 200 * DAY - 1),
                                 Config(lookback=14, skip=2), 400.0,
                                 signal_fn=index_signal)
    store.close()
    assert isinstance(dec, Skip), dec
    assert dec.reason == "unhedgeable_beta", dec
    assert "SE exceeds estimate" in dec.detail, dec.detail
    print(f"PASS unhedgeable_when_se_exceeds_estimate ({dec.detail})")


# ----------------------------------------------------------------- test 23

def test_delisting_is_not_a_data_gap():
    """Stage 2e Test 23. One fixture, two events: a symbol that delists with
    a known settlement price, and a symbol whose bars simply stop for a
    while. Different code paths, different log reasons, different PnL."""
    n_days = 260
    closes, _ = factor_market(seed=51, n_days=n_days)
    delisted, gapped = "ALT02USDT", "ALT11USDT"
    gap_start, gap_len = 180, 2            # short gap: recoverable
    delist_day = 200

    # holes: the delisted symbol stops for good; the gapped one resumes
    holes = {delisted: range(delist_day, n_days),
             gapped: range(gap_start, gap_start + gap_len)}
    store = _store_with_holes(closes, holes)
    cfg = Config(lookback=14, skip=2, initial_capital=400.0)

    settle_px = float(closes[delisted][delist_day - 1]) * 0.40   # 60% haircut
    meta = {delisted: (T0 + delist_day * DAY - 1, settle_px)}
    res = run_backtest(store, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1,
                       signal_fn=index_signal, delistings=meta)

    # delisting: recorded as a delisting, at the settlement price, not estimated
    assert len(res.delistings) == 1, res.delistings
    ts, sym, price, estimated = res.delistings[0]
    assert sym == delisted and not estimated
    assert math.isclose(price, settle_px, rel_tol=1e-12)
    assert not any(s_ == delisted for _, s_, _ in res.data_gap_exits)

    # data gap: held through, never exited (2 days < 3-day tolerance)
    assert not any(s_ == gapped for _, s_, _ in res.data_gap_exits), res.data_gap_exits
    # ...and it contributed no PnL on the missing days
    idx = {t_: i for i, t_ in enumerate(res.timestamps)}
    for d in range(gap_start, gap_start + gap_len):
        t_ = T0 + (d + 1) * DAY - 1
        if t_ in idx:
            assert gapped not in res.pnl_by_symbol_day[idx[t_]]

    # the settlement price actually moved PnL: same fixture, no metadata, so
    # the delisted symbol becomes a data-gap exit at last mark instead
    store2 = _store_with_holes(closes, holes)
    res2 = run_backtest(store2, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1,
                        signal_fn=index_signal)
    assert len(res2.delistings) == 0
    assert any(s_ == delisted for _, s_, _ in res2.data_gap_exits), res2.data_gap_exits
    assert res.gross_pnl != res2.gross_pnl, "the two paths must price differently"

    # a delisting with no recorded settlement price is flagged estimated
    store3 = _store_with_holes(closes, holes)
    res3 = run_backtest(store3, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1,
                        signal_fn=index_signal,
                        delistings={delisted: (T0 + delist_day * DAY - 1, None)})
    assert len(res3.delistings) == 1 and res3.delistings[0][3] is True

    # a gap longer than the tolerance does force an exit, logged as such
    long_holes = {gapped: range(gap_start, gap_start + 10)}
    store4 = _store_with_holes(closes, long_holes)
    res4 = run_backtest(store4, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1,
                        signal_fn=index_signal)
    exits = [(s_, n) for _, s_, n in res4.data_gap_exits if s_ == gapped]
    assert exits, res4.data_gap_exits
    assert exits[0][1] == cfg.max_data_gap_days + 1, exits
    assert len(res4.delistings) == 0
    print(f"PASS delisting_is_not_a_data_gap (delist @ ${settle_px:.2f} vs "
          f"gap held {gap_len}d; long gap exits after "
          f"{cfg.max_data_gap_days + 1}d; PnL {res.gross_pnl:.2f} vs "
          f"{res2.gross_pnl:.2f})")


def _store_with_holes(closes, holes: dict):
    """build_store, but each symbol in `holes` is missing those day indices."""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    filters = []
    for sym, cl in closes.items():
        cl = np.asarray(cl, dtype=float)
        op = np.concatenate([[cl[0]], cl[:-1]])
        skip_days = set(holes.get(sym, ()))
        rows, frows = [], []
        for d in range(len(cl)):
            if d in skip_days:
                continue
            ot = T0 + d * DAY
            rows.append((ot, ot + DAY - 1, op[d], max(op[d], cl[d]) * 1.001,
                         min(op[d], cl[d]) * 0.999, cl[d], 10.0, 2e7, 100))
            for h in range(3):
                frows.append((ot + h * H8, 1e-4))
        store.insert_klines(sym, "1d", rows)
        store.insert_klines(sym, "1m", _minute_rows(rows, 0.0))
        store.insert_funding(sym, frows)
        filters.append({"symbol": sym, "status": "TRADING", "min_notional": 1.0})
    store.insert_filters(T0, filters)
    return store


def test_maker_mode_is_not_reportable():
    """Stage 2e 4: the backtester treats a maker fee as a guaranteed fill and
    has no fill-probability model, so a maker-mode result must not be
    reportable. Taker is unaffected; an explicitly non-reportable
    exploratory purpose is the only way through."""
    from backtest.runner import assert_reportable_fee_mode

    taker = Config(lookback=14, skip=2)
    maker = Config(lookback=14, skip=2, fee_mode="maker")
    assert_reportable_fee_mode(taker, "grid")            # fine
    try:
        assert_reportable_fee_mode(maker, "grid")
    except ValueError as e:
        assert "fill-probability" in str(e)
    else:
        raise AssertionError("maker mode must not be reportable")
    # the escape hatch exists but names itself
    assert_reportable_fee_mode(maker, "exploratory-nonreportable-maker-check")
    print("PASS maker_mode_is_not_reportable")


def test_funding_interval_inferred_per_symbol():
    """Stage 2e 6: a 4-hourly symbol must be scored against six settlements a
    day, not three. The cadence is inferred from the symbol's own timestamps
    (the dataset has no interval column), so a 4-hourly symbol stops
    reporting spurious missing settlements."""
    from backtest import costs

    n_days = 120
    closes = {BTC: np.full(n_days, 20_000.0)}
    for i in range(14):
        closes[f"ALT{i:02d}USDT"] = np.full(n_days, 100.0)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    four_h = 4 * 3_600_000
    filters = []
    for sym, cl in closes.items():
        rows, frows = [], []
        for d in range(n_days):
            ot = T0 + d * DAY
            rows.append((ot, ot + DAY - 1, cl[d], cl[d] * 1.001, cl[d] * 0.999,
                         cl[d], 10.0, 2e7, 100))
            step = four_h if sym == "ALT00USDT" else H8
            for h in range(DAY // step):
                frows.append((ot + h * step, 1e-4))
        store.insert_klines(sym, "1d", rows)
        store.insert_klines(sym, "1m", _minute_rows(rows, 0.0))
        store.insert_funding(sym, frows)
        filters.append({"symbol": sym, "status": "TRADING", "min_notional": 1.0})
    store.insert_filters(T0, filters)

    store.reset_clock()
    v = store.view_as_of(T0 + 100 * DAY - 1)
    assert costs.infer_funding_interval_ms(v, "ALT00USDT") == four_h
    assert costs.infer_funding_interval_ms(v, "ALT01USDT") == H8
    # a symbol with no funding at all falls back to the 8h default
    assert costs.infer_funding_interval_ms(v, "NOSUCHUSDT") == H8
    store.close()

    # counting respects the interval
    assert costs.expected_settlement_count(T0 - 1, T0 + DAY - 1) == 3
    assert costs.expected_settlement_count(T0 - 1, T0 + DAY - 1, four_h) == 6
    assert costs.settlement_times(T0 - 1, T0 + DAY - 1, four_h)[:2] == [T0, T0 + four_h]
    print("PASS funding_interval_inferred_per_symbol")


def test_intraday_stress_and_bootstrap_ci():
    """Stage 2e 7 and 9. The H/L stress path must be no better than the
    close-to-close equity and must react to a violent intraday wick; the
    bootstrap CI must bracket the point estimate and widen with noise."""
    res = shared_factor_run()
    assert len(res.daily_worst_equity) == len(res.timestamps)
    eq_by_ts = dict(zip(res.timestamps, res.equity))
    for ts, worst in res.daily_worst_equity:
        assert worst <= eq_by_ts[ts] + 1e-9, (ts, worst, eq_by_ts[ts])

    # a fixture with huge intraday wicks must show a materially worse path
    closes, _ = factor_market(seed=61, n_days=300)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    filters = []
    for sym, cl in closes.items():
        cl = np.asarray(cl, dtype=float)
        op = np.concatenate([[cl[0]], cl[:-1]])
        rows, frows = [], []
        for d in range(len(cl)):
            ot = T0 + d * DAY
            rows.append((ot, ot + DAY - 1, op[d], max(op[d], cl[d]) * 1.5,
                         min(op[d], cl[d]) * 0.5, cl[d], 10.0, 2e7, 100))
            for h in range(3):
                frows.append((ot + h * H8, 1e-4))
        store.insert_klines(sym, "1d", rows)
        store.insert_klines(sym, "1m", _minute_rows(rows, 0.0))
        store.insert_funding(sym, frows)
        filters.append({"symbol": sym, "status": "TRADING", "min_notional": 1.0})
    store.insert_filters(T0, filters)
    wild = run_backtest(store, CFG, T0 + DAY - 1, T0 + 300 * DAY - 1)
    store.close()
    wmin = min(e for _, e in wild.daily_worst_equity)
    wclose = min(wild.equity)
    assert wmin < wclose, (wmin, wclose)

    # bootstrap CI brackets the point estimate on a real return series
    _, eq = metrics.strategy_window(res)
    rets = metrics.daily_returns(eq)
    sr = metrics.sharpe(rets)
    lo, hi = metrics.sharpe_bootstrap_ci(rets, seed=1)
    assert lo < sr < hi, (lo, sr, hi)
    assert hi - lo > 0.1
    # deterministic for a given seed, and wider than the naive parametric SE
    assert metrics.sharpe_bootstrap_ci(rets, seed=1) == (lo, hi)
    # too little data -> NaN rather than a fabricated interval
    assert all(math.isnan(x) for x in metrics.sharpe_bootstrap_ci(rets[:10]))
    print(f"PASS intraday_stress_and_bootstrap_ci (worst path {wmin:.0f} vs "
          f"close-only {wclose:.0f}; sharpe {sr:.2f} CI [{lo:.2f}, {hi:.2f}])")


# ----------------------------------------------------------------- test 22

def test_fills_at_the_delayed_minute_open():
    """Stage 2e Test 22. The fill price is the 00:01 open -- never the 00:00
    open, never the signal bar's close. The minute bars are PIT-gated like
    every other bar, and a missing 00:01 falls FORWARD, never back."""
    n_days = 200
    closes, _ = factor_market(seed=71, n_days=n_days)
    drift = 0.01                      # minute m opens 1% above minute m-1
    store = build_store(closes, minute_drift=drift)
    cfg = Config(lookback=14, skip=2, initial_capital=400.0)
    res = run_backtest(store, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1)
    assert res.rebalances and res.total_fees > 0, "vacuous: nothing filled"

    daily_open, minute_open, sig_close = {}, {}, {}
    for rb in res.rebalances[:40]:
        d = day_of(rb.ts_fill)
        store.reset_clock()
        v = store.view_as_of(rb.ts_fill)
        for sym, (delta, price) in rb.fills.items():
            bar = [b for b in v.klines(sym, "1d", limit=2) if day_of(b.close_time) == d][0]
            mins = {b.open_time: b for b in v.klines(sym, "1m", limit=5)}
            m1 = mins[T0 + d * DAY + 60_000]
            # slippage is off here, so the fill IS the 00:01 open
            assert math.isclose(price, m1.open, rel_tol=1e-12), (price, m1.open)
            assert not math.isclose(price, bar.open, rel_tol=1e-9), "filled at 00:00"
            daily_open[sym] = bar.open
            minute_open[sym] = m1.open
            prev = v.klines(sym, "1d", limit=2)[0]
            sig_close[sym] = prev.close
            assert not math.isclose(price, prev.close, rel_tol=1e-9), "filled at signal close"
    assert minute_open and all(
        minute_open[k] > daily_open[k] for k in minute_open), "fixture drift not applied"

    # PIT gating: the 00:01 bar of day D is invisible at the close of D-1.
    store.reset_clock()
    v_prev = store.view_as_of(T0 + 100 * DAY - 1)          # close of day 99
    sym = "ALT00USDT"
    seen = {b.open_time for b in v_prev.klines(sym, "1m", limit=50)}
    assert (T0 + 100 * DAY + 60_000) not in seen, "day-100 00:01 visible at day-99 close"
    assert max(seen) < T0 + 100 * DAY, "minute bars leaked past as_of"
    store.close()

    # execution_delay_minutes = 0 reproduces the old 00:00 convention exactly
    store0 = build_store(closes, minute_drift=drift)
    res0 = run_backtest(store0, Config(lookback=14, skip=2, initial_capital=400.0,
                                       execution_delay_minutes=0),
                        T0 + DAY - 1, T0 + n_days * DAY - 1)
    store0.close()
    rb0 = res0.rebalances[0]
    store0b = build_store(closes, minute_drift=drift)
    store0b.reset_clock()
    v0 = store0b.view_as_of(rb0.ts_fill)
    for sym_, (_, price) in rb0.fills.items():
        bar = [b for b in v0.klines(sym_, "1d", limit=2)
               if day_of(b.close_time) == day_of(rb0.ts_fill)][0]
        assert math.isclose(price, bar.open, rel_tol=1e-12)
    store0b.close()
    assert res0.gross_pnl != res.gross_pnl, "the delay must change results"

    # A missing 00:01 falls forward to 00:02 -- never back to 00:00.
    holes = {"ALT00USDT": {1}}          # minute index 1 absent every day
    store2 = _store_missing_minutes(closes, holes, drift)
    res2 = run_backtest(store2, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1)
    assert res2.minute_fill_fallbacks > 0, "fallback never exercised"
    rb2 = next(r for r in res2.rebalances if "ALT00USDT" in r.fills)
    store2.reset_clock()
    v2 = store2.view_as_of(rb2.ts_fill)
    d2 = day_of(rb2.ts_fill)
    mins2 = {b.open_time: b for b in v2.klines("ALT00USDT", "1m", limit=5)}
    price2 = rb2.fills["ALT00USDT"][1]
    assert math.isclose(price2, mins2[T0 + d2 * DAY + 2 * 60_000].open, rel_tol=1e-12)
    assert not math.isclose(price2, mins2[T0 + d2 * DAY].open, rel_tol=1e-9), \
        "fell BACK to 00:00"
    store2.close()

    # No acceptable minute at all -> the rebalance is skipped, never filled
    # at 00:00. (missing_fill_bar is the same rule as a missing daily bar.)
    store3 = _store_missing_minutes(closes, {s_: {1, 2, 3} for s_ in closes}, drift)
    res3 = run_backtest(store3, cfg, T0 + DAY - 1, T0 + n_days * DAY - 1)
    store3.close()
    assert not res3.rebalances, "filled without an execution bar"
    assert any(r == "missing_fill_bar" for _, r, _ in res3.skips), res3.skip_counts()
    print(f"PASS fills_at_the_delayed_minute_open ({len(res.rebalances)} fills at "
          f"+1min; {res2.minute_fill_fallbacks} fell forward to +2min; "
          f"no-minute run skipped {len(res3.skips)}x)")


def _store_missing_minutes(closes, holes: dict, drift: float):
    """build_store, but the given minute indices are absent per symbol."""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = PointInTimeStore(tmp.name)
    filters = []
    for sym, cl in closes.items():
        cl = np.asarray(cl, dtype=float)
        op = np.concatenate([[cl[0]], cl[:-1]])
        rows, frows = [], []
        for d in range(len(cl)):
            ot = T0 + d * DAY
            rows.append((ot, ot + DAY - 1, op[d], max(op[d], cl[d]) * 1.001,
                         min(op[d], cl[d]) * 0.999, cl[d], 10.0, 2e7, 100))
            for h in range(3):
                frows.append((ot + h * H8, 1e-4))
        store.insert_klines(sym, "1d", rows)
        drop = holes.get(sym, set())
        store.insert_klines(sym, "1m", [
            r for r in _minute_rows(rows, drift)
            if ((r[0] % DAY) // 60_000) not in drop
        ])
        store.insert_funding(sym, frows)
        filters.append({"symbol": sym, "status": "TRADING", "min_notional": 1.0})
    store.insert_filters(T0, filters)
    return store


def test_funding_settlements_tolerate_millisecond_offsets():
    """Stage 3 1.1: Binance stamps a settlement a few ms PAST its boundary
    (45.7% of real rows are off by 1-6ms). Bucketing on the raw stamp both
    counted the 00:00 settlement as missing and applied it to the post-fill
    book instead of the book held across midnight. Settlements must snap to
    the boundary they belong to."""
    n_days = 120
    # BTC must move or every beta is 0/0 (btc_zero_variance); it is kept
    # out of the universe by volume, as in the other flat fixtures.
    closes = {BTC: 20_000 * (1 + 0.01 * (-1.0) ** np.arange(n_days))}
    for i in range(14):
        closes[f"ALT{i:02d}USDT"] = np.full(n_days, 100.0)

    def build(offset_ms: int):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        st = PointInTimeStore(tmp.name)
        filters = []
        for sym, cl in closes.items():
            i = int(sym[3:5]) if sym.startswith("ALT") else -1
            rate = 1e-3 if 0 <= i <= 4 else (-1e-3 if i >= 9 else 0.0)
            rows, frows = [], []
            for d in range(n_days):
                ot = T0 + d * DAY
                qv = 1.0 if sym == BTC else 2e7
                rows.append((ot, ot + DAY - 1, cl[d], cl[d] * 1.001,
                             cl[d] * 0.999, cl[d], 10.0, qv, 100))
                for h in range(3):
                    # asymmetric by leg so the total is clearly nonzero:
                    # equal rates would cancel and make the check vacuous
                    frows.append((ot + h * H8 + offset_ms, rate))
            st.insert_klines(sym, "1d", rows)
            st.insert_klines(sym, "1m", _minute_rows(rows))
            st.insert_funding(sym, frows)
            filters.append({"symbol": sym, "status": "TRADING", "min_notional": 1.0})
        st.insert_filters(T0, filters)
        return st

    exact = run_backtest(build(0), CFG, T0 + DAY - 1, T0 + n_days * DAY - 1)
    offset = run_backtest(build(6), CFG, T0 + DAY - 1, T0 + n_days * DAY - 1)
    assert exact.rebalances and offset.rebalances, "vacuous"
    # a few ms of stamp jitter must change nothing that matters
    assert offset.missing_funding_settlements == exact.missing_funding_settlements == 0, (
        offset.missing_funding_settlements, exact.missing_funding_settlements)
    assert math.isclose(offset.total_funding, exact.total_funding, rel_tol=1e-9), (
        offset.total_funding, exact.total_funding)
    assert exact.total_funding < -1.0, "fixture must produce real funding flow"
    assert offset.funding_notional_missing == 0.0
    print(f"PASS funding_settlements_tolerate_millisecond_offsets "
          f"(funding {exact.total_funding:+.4f} either way, 0 missing)")


# ------------------------------------------------------- metrics sanity

def test_deflated_sharpe_sanity():
    assert math.isnan(metrics.deflated_sharpe(0.1, 500, [0.1], 0.0, 3.0))
    trials = [0.02, -0.01, 0.05, 0.0, 0.03]
    lo = metrics.deflated_sharpe(0.02, 500, trials, 0.0, 3.0)
    hi = metrics.deflated_sharpe(0.10, 500, trials, 0.0, 3.0)
    assert 0.0 <= lo <= hi <= 1.0
    assert hi > lo, "DSR must increase with observed Sharpe"
    print("PASS deflated_sharpe_sanity")


# ------------------------------------------------ runner: guards & logging

def test_runner_trial_log_and_holdout_guard():
    """The holdout refuses without the flag, records the look BEFORE
    running, and refuses forever after. Every execution logs a trial with
    the git commit. Paths are redirected so nothing real is touched."""
    import argparse
    import json

    from backtest import runner

    scratch = Path(tempfile.mkdtemp())
    orig_trials, orig_holdout = runner.TRIALS_PATH, runner.HOLDOUT_LOG
    runner.TRIALS_PATH = scratch / "trials.jsonl"
    runner.HOLDOUT_LOG = scratch / "holdout_log.json"
    try:
        cfg = Config(lookback=7, skip=0)
        # trial logging carries commit + hash + split + purpose
        runner.log_trial(cfg, "train", "unit", {"sharpe": 0.1, "max_dd": 0.2})
        rec = json.loads(runner.TRIALS_PATH.read_text().strip())
        assert rec["config_hash"] == runner.config_hash(cfg)
        assert rec["split"] == "train" and rec["purpose"] == "unit"
        assert "git_commit" in rec and rec["sharpe"] == 0.1
        assert runner.trial_srs_for_deflation("train") == [
            0.1 / math.sqrt(metrics.ANN)]

        # holdout: no flag -> refuse
        ns = argparse.Namespace(i_understand_this_is_the_only_look=False)
        try:
            runner.check_holdout_guard(ns, cfg)
        except SystemExit:
            pass
        else:
            raise AssertionError("holdout ran without the flag")
        assert not runner.HOLDOUT_LOG.exists()

        # with flag -> look recorded as 'started' before any result exists
        ns = argparse.Namespace(i_understand_this_is_the_only_look=True)
        runner.check_holdout_guard(ns, cfg)
        log = json.loads(runner.HOLDOUT_LOG.read_text())
        assert log["runs"][0]["status"] == "started"
        runner.record_holdout_result({"sharpe": 0.3})
        assert json.loads(runner.HOLDOUT_LOG.read_text())["runs"][0][
            "result"]["sharpe"] == 0.3

        # second look, even with the flag -> abort
        try:
            runner.check_holdout_guard(ns, cfg)
        except SystemExit:
            pass
        else:
            raise AssertionError("holdout allowed a second look")

        # report + summarise must not crash on a real result (smoke)
        res = shared_factor_run()
        s = runner.summarise(res)
        assert s["n_rebalances"] == len(res.rebalances)
        runner.report(res, "train")
    finally:
        runner.TRIALS_PATH, runner.HOLDOUT_LOG = orig_trials, orig_holdout
    print("PASS runner_trial_log_and_holdout_guard")


def test_runner_diagnose_never_logs_a_trial():
    """The drift decomposition writes diagnostics.jsonl and must never touch
    trials.jsonl — attribution is not a trial. Also a crash guard for the
    path that runs after the grid."""
    from backtest import runner
    from tools.build_demeaned_db import build_demeaned_db

    closes, _ = factor_market(seed=8, n_days=200)   # T0 lies inside 'train'
    src = build_store(closes)
    src_path = src.path
    src.close()
    dst_path = src_path + ".dm.db"
    build_demeaned_db(src_path, dst_path)

    scratch = Path(tempfile.mkdtemp())
    orig = (runner.DIAGNOSTICS_PATH, runner.TRIALS_PATH)
    runner.DIAGNOSTICS_PATH = scratch / "diagnostics.jsonl"
    runner.TRIALS_PATH = scratch / "trials.jsonl"
    try:
        rec = runner.drift_decomposition(
            CFG, Path(src_path), Path(dst_path), split="train")
        assert rec["kind"] == "drift_decomposition"
        assert rec["note"] == runner.DIAGNOSTIC_NOTE
        assert rec["real"]["n_rebalances"] > 0
        assert rec["demeaned"]["n_rebalances"] > 0
        assert runner.DIAGNOSTICS_PATH.exists()
        assert not runner.TRIALS_PATH.exists(), "diagnostic logged a TRIAL"
    finally:
        runner.DIAGNOSTICS_PATH, runner.TRIALS_PATH = orig
    print("PASS runner_diagnose_never_logs_a_trial")


# ----------------------------------------------------------------- test 11

def test_stage1_regression():
    """tests/test_lookahead.py must still be 13/13. Stage 1 is untouched."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "test_lookahead.py")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "13/13 passed" in proc.stdout, proc.stdout
    print("PASS stage1_regression (13/13)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
