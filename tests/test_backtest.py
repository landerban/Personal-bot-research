"""
Backtester tests. The null tests (1, 2, 3) matter most: they are not about
proving the strategy works, they are about proving the HARNESS does not
manufacture edge. If a random signal is profitable, the harness has a bug.

All tests run against synthetic stores built through the same
PointInTimeStore/PITView machinery as production — no mocking of Stage 1.
"""

from __future__ import annotations

import math
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


# ---------------------------------------------------------------- fixtures

def build_store(
    closes: dict[str, np.ndarray],
    opens: dict[str, np.ndarray] | None = None,
    quote_volumes: dict[str, float] | None = None,
    funding_rates: dict[str, float] | None = None,
    min_notionals: dict[str, float] | None = None,
) -> PointInTimeStore:
    """Synthetic market. Default open_d = close_{d-1} (continuous prints)."""
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
        sum(rb.fees for rb in res.rebalances), res.total_fees, rel_tol=1e-12
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
