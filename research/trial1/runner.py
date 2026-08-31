"""
Trial 1 of 20 — the run stage (NOTES §65; criteria locked in §60.12 + §64).

This is the §59.1 Gen-2 research runner: every read is seal-routed and
hard-capped at 2024-12-31, so the sealed interval is structurally
unreachable. It ASSEMBLES the frozen rcm modules on real development data
— it decides nothing. Every assembly reading it relies on is recorded in
NOTES §65.2 BEFORE this file existed.

The runner produces: the exhaustive daily calendar (formed / gate /
structural / operational / degenerate with causes), the criterion inputs
for the two locked evaluators, the §64.3 reporting objects, and one
summary JSON. The verdict is whatever the evaluators return.
"""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_left, bisect_right
from pathlib import Path

import numpy as np

from backtest.sizing import SymbolFilters, size_from_weight
from backtest.universe_filter import classify
from rcm.attribution import delta_gate, delta_transition, reporting_tuple
from rcm.eval_formation import DayRecord
from rcm.factors import estimate_betas, orthogonalize_eth, residual_series
from rcm.funding import FundingUnobservable, forecast
from rcm.gates import (COVERAGE_NA, DegenerateTarget, GateConfig,
                       IntegrityFailure, evaluate)
from rcm.momentum import (CalibrationSet, CarryGuard, SHRINK_N0, calibrate,
                          raw_score, zscores)
from rcm.optimizer import OperationalFailure, degenerate_cause, solve
from rcm.rescov import FullResidualCovarianceModel, estimate
from rcm.seal import assert_range_allowed
from rcm.statemachine import Calendar, transition
from rcm.timeline import (decision_cutoff_ms, exec_time_ms,
                          newest_admissible_signal_day)

DAY = 86_400_000
ERA_START = 1_577_836_800_000            # 2020-01-01 UTC
ERA_END = 1_735_603_200_000              # 2024-12-31 UTC
LEAD_START = ERA_START - 200 * DAY       # 180d history rule needs lead-in
MIN_HISTORY = 180                        # §59.3.2 frozen
WINDOW = 91                              # 91 closes -> 90 returns (§60.1)
SIGMA_D = 0.10 / np.sqrt(365.0)          # frozen 10% ann vol target
CAPITAL = 800.0                          # NOTES §65.1 USER DECISION
ETA = 0.0010                             # frozen cost stack, reporting line
FACTORS = ("BTCUSDT", "ETHUSDT")
DB = Path(__file__).resolve().parents[2] / "xsmom.db"
OUT = Path(__file__).resolve().parent / "out"

_CAT = {Calendar.FORMED: "formed", Calendar.GATE: "gate",
        Calendar.STRUCTURAL: "structural", Calendar.OPERATIONAL: "operational",
        Calendar.DEGENERATE: "degenerate"}


class FundingView:
    """The PITView-shaped funding interface rcm.funding.forecast needs:
    `.as_of` and `.funding(symbol, since=...)` -> [(time, rate), ...] with
    time <= as_of. Backed by presorted arrays; no store read after load."""

    def __init__(self, rows_by_sym: dict, as_of: int):
        self._rows = rows_by_sym
        self.as_of = as_of

    def funding(self, symbol: str, since: int):
        times, rates = self._rows.get(symbol, ((), ()))
        lo = bisect_right(times, since)          # strictly after `since`
        hi = bisect_right(times, self.as_of)
        return [(times[k], rates[k]) for k in range(lo, hi)]


def load_store(db_path=DB, classify_fn=None):
    """One seal-checked read of everything the era needs. The end of every
    range is strictly before the seal; assert_range_allowed enforces it."""
    kline_end = ERA_END + 4 * 60_000         # through the 00:03 fallback bar
    funding_end = ERA_END + 60_000           # the 2024-12-31 00:00 settlement
    assert_range_allowed(LEAD_START, max(kline_end, funding_end))
    if classify_fn is None:
        classify_fn = lambda s: classify(s).eligible  # noqa: E731

    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        closes: dict[str, dict[int, float]] = {}
        for sym, ot, close in con.execute(
                "SELECT symbol, open_time, close FROM klines WHERE "
                "interval='1d' AND open_time>=? AND open_time<=?",
                (LEAD_START, ERA_END)):
            closes.setdefault(sym, {})[int(ot)] = float(close)
        execs: dict[str, dict[int, float]] = {}
        for sym, ot, op in con.execute(
                "SELECT symbol, open_time, open FROM klines WHERE "
                "interval='1m' AND open_time>=? AND open_time<=?",
                (ERA_START, kline_end)):
            ot = int(ot)
            day, off = ot - ot % DAY, ot % DAY
            if off in (60_000, 120_000, 180_000):
                d = execs.setdefault(sym, {})
                if day not in d or off < d[day][0]:
                    d[day] = (off, float(op))
        execs = {s: {d: px for d, (off, px) in v.items()}
                 for s, v in execs.items()}
        # Gen-1 §19.1 precedent applied at the view layer: Binance stamps a
        # settlement a few ms PAST its boundary (45.7% of rows). Snap each
        # stamp to the nearest 4h grid point when within 60s — 8h boundaries
        # are 4h multiples, so both cadences survive and the §60.11.1
        # equality-observability arithmetic sees clean boundaries. Without
        # this, ~a quarter of name-days fail observability on stamp jitter
        # alone — the exact bug class §19.1 measured and fixed.
        four_h = 4 * 3_600_000
        fund: dict[str, tuple] = {}
        n_snapped = 0
        for sym, ft, fr in con.execute(
                "SELECT symbol, funding_time, funding_rate FROM funding "
                "WHERE funding_time>=? AND funding_time<=? "
                "ORDER BY symbol, funding_time", (LEAD_START, funding_end)):
            ft = int(ft)
            b = round(ft / four_h) * four_h
            if ft != b and abs(ft - b) < 60_000:
                ft = b
                n_snapped += 1
            fund.setdefault(sym, ([], []))
            fund[sym][0].append(ft)
            fund[sym][1].append(float(fr))
        fund = {s: (tuple(t), tuple(r)) for s, (t, r) in fund.items()}
        filters: dict[str, SymbolFilters] = {}
        for sym, mn, ss, ts in con.execute(
                "SELECT symbol, min_notional, step_size, tick_size "
                "FROM symbol_filters"):
            filters[sym] = SymbolFilters(sym, mn, ss, ts)
    finally:
        con.close()

    class_ok = sorted(s for s in closes
                      if s not in FACTORS and classify_fn(s))
    times = {s: np.array(sorted(closes[s]), dtype=np.int64)
             for s in list(class_ok) + list(FACTORS)}
    sets = {s: set(closes[s]) for s in times}
    return {"closes": closes, "execs": execs, "fund": fund,
            "filters": filters, "class_ok": class_ok,
            "times": times, "sets": sets, "n_snapped": n_snapped}


def _window_returns(closes, sym, window_days):
    px = np.array([closes[sym][d] for d in window_days])
    return px[1:] / px[:-1] - 1.0


def _exec_ret(execs, sym, t):
    a = execs.get(sym, {}).get(t)
    b = execs.get(sym, {}).get(t + DAY)
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1.0


def run(store, era_start=ERA_START, era_end=ERA_END, out_dir=OUT,
        calib_check_every=120) -> dict:
    closes, execs, fund = store["closes"], store["execs"], store["fund"]
    filters, class_ok = store["filters"], store["class_ok"]
    times, sets = store["times"], store["sets"]

    # incremental calibration sums (§65.2.5) + the full obs list for the
    # frozen-builder equivalence asserts and the IC evaluator
    obs_all: list[dict] = []
    obs_ptr = 0
    s_zz = s_ze = 0.0
    n_cs = 0

    guard = CarryGuard()
    held: dict[str, float] = {}
    g_ref: float | None = None
    consec = 0

    day_records: list[DayRecord] = []
    ic_records: list[dict] = []
    daily_rows: list[dict] = []
    calendar: list[Calendar] = []
    shadow: list[float] = []
    actual: list[float] = []
    gate_counts: dict[str, int] = {}
    price_pnl = fund_pnl = cost_pnl = 0.0
    turnover_total = 0.0
    n_calib_checked = 0

    last_shadow_day = era_end - DAY      # §65: the era-end day's forward
                                          # interval would cross the seal

    out_dir.mkdir(parents=True, exist_ok=True)
    fh = (out_dir / "daily.jsonl").open("w", encoding="utf-8")

    t = era_start
    while t <= era_end:
        row: dict = {"t_ms": t}
        cat = None
        cause = ""
        capable = False
        w_pre = w_real = None
        symbols: list[str] = []
        mu_mom = f_hat = None
        cov = None
        try:
            window_days = [t - k * DAY for k in range(WINDOW, 0, -1)]
            factor_ok = all(d in sets[s] for s in FACTORS
                            for d in window_days)
            # §63.2.2 structural eligibility (IC universe)
            struct = [s for s in class_ok
                      if int(np.searchsorted(times[s], t)) >= MIN_HISTORY
                      and all(d in sets[s] for d in window_days)]
            row["n_struct"] = len(struct)
            if not factor_ok or len(struct) < 2:
                cat, cause = Calendar.STRUCTURAL, "insufficient_universe"
                raise StopIteration

            f_btc = _window_returns(closes, "BTCUSDT", window_days)
            f_eth = _window_returns(closes, "ETHUSDT", window_days)
            f_ethp = orthogonalize_eth(f_btc, f_eth)
            x = f_btc - f_btc.mean()
            eth_slope = float(x @ (f_eth - f_eth.mean())) / float(x @ x)
            design = np.column_stack([np.ones(WINDOW - 1), f_btc, f_ethp])
            xtx_inv = np.linalg.inv(design.T @ design)
            g_btc, g_eth = float(xtx_inv[1, 1]), float(xtx_inv[2, 2])
            sf_btc = float(f_btc.std(ddof=1))
            sf_eth = float(f_ethp.std(ddof=1))

            betas, resids, keep = {}, {}, []
            for s in struct:
                r = _window_returns(closes, s, window_days)
                b = estimate_betas(r, f_btc, f_ethp)
                if b.sigma_eps <= 0.0:
                    continue                      # §63.2.2.5, counted below
            # (second loop keeps arrays aligned after the zero-var drop)
                betas[s] = b
                resids[s] = residual_series(r, f_btc, f_ethp, b)
                keep.append(s)
            row["n_zero_var"] = len(struct) - len(keep)
            struct = keep
            if len(struct) < 2:
                cat, cause = Calendar.STRUCTURAL, "insufficient_universe"
                raise StopIteration

            m_scores = {s: raw_score(resids[s]) for s in struct}
            z_struct = zscores(np.array([m_scores[s] for s in struct]))

            # ε_fwd for signal day t (IC + future calibration) — only while
            # the forward interval stays inside the era (§65: seal)
            if t <= last_shadow_day:
                rb = _exec_ret(execs, "BTCUSDT", t)
                re_ = _exec_ret(execs, "ETHUSDT", t)
                if rb is not None and re_ is not None:
                    fe = re_ - eth_slope * rb
                    zs, es = [], []
                    for k, s in enumerate(struct):
                        ri = _exec_ret(execs, s, t)
                        if ri is None:
                            continue
                        b = betas[s]
                        zs.append(float(z_struct[k]))
                        es.append(ri - b.beta_btc * rb - b.beta_eth_perp * fe)
                    if len(zs) >= 2:
                        ob = {"signal_day_ms": t, "z": np.array(zs),
                              "eps_fwd": np.array(es)}
                        obs_all.append(ob)
                        ic_records.append({"date_ms": t,
                                           "z_mom": ob["z"],
                                           "eps_fwd": ob["eps_fwd"]})

            # calibration slope at t: admit newly-closed cross-sections
            newest = newest_admissible_signal_day(t)
            while obs_ptr < len(obs_all) and \
                    obs_all[obs_ptr]["signal_day_ms"] <= newest:
                ob = obs_all[obs_ptr]
                zc = ob["z"] - ob["z"].mean()
                ec = ob["eps_fwd"] - ob["eps_fwd"].mean()
                s_zz += float(zc @ zc)
                s_ze += float(zc @ ec)
                n_cs += 1
                obs_ptr += 1
            b_hat = s_ze / s_zz if s_zz > 0 else 0.0
            b_shrunk = (n_cs / (n_cs + SHRINK_N0)) * b_hat
            b_tilde = 0.0 if b_shrunk <= 0 else b_shrunk
            row["b_tilde"] = b_tilde
            row["n_cs"] = n_cs
            if calib_check_every and (len(daily_rows) % calib_check_every
                                      == 0) and n_cs > 0:
                ref = calibrate(CalibrationSet.build(obs_all, t))
                assert abs(ref.b_hat - b_hat) < 1e-12 and \
                    ref.n_cross_sections == n_cs and \
                    abs(ref.b_tilde - b_tilde) < 1e-12, \
                    "incremental calibration diverged from the frozen builder"
                n_calib_checked += 1

            # funding/data eligibility (§62.8.2 ordering)
            view = FundingView(fund, decision_cutoff_ms(t))
            t_ex, t1_ex = exec_time_ms(t), exec_time_ms(t + DAY)
            f_map = {}
            for s in struct:
                try:
                    f_map[s] = forecast(view, s, t_ex, t1_ex).total
                except FundingUnobservable:
                    continue
            symbols = sorted(f_map)
            row["n_eligible"] = len(symbols)
            row["n_funding_dropped"] = len(struct) - len(symbols)
            capable = True
            if len(symbols) < 2:
                cat, cause = Calendar.STRUCTURAL, "insufficient_universe"
                raise StopIteration

            z_strat = zscores(np.array([m_scores[s] for s in symbols]))
            mu_mom = b_tilde * z_strat
            f_hat = np.array([f_map[s] for s in symbols])
            mu_total = mu_mom - f_hat

            res = estimate(np.corrcoef(
                np.vstack([resids[s] for s in symbols])),
                np.array([betas[s].sigma_eps ** 2 for s in symbols]))
            cov = FullResidualCovarianceModel.create(
                np.array([betas[s].beta_btc for s in symbols]),
                np.array([betas[s].beta_eth_perp for s in symbols]),
                sf_btc, sf_eth, res.omega, g_btc, g_eth)
            row.update(res.report())

            w_prev = np.array([held.get(s, 0.0) for s in symbols])
            out = solve(symbols, mu_total, cov, None, None, SIGMA_D,
                        w_prev=w_prev)
            w_pre = out.w
            if out.gross == 0.0:
                cat = Calendar.DEGENERATE
                cause = degenerate_cause(symbols, mu_total, cov, None, None,
                                         SIGMA_D)
                raise StopIteration

            # sizing at the frozen $800 (§65.1); reference price close(t-1)
            w_real = np.zeros_like(w_pre)
            for k, s in enumerate(symbols):
                if w_pre[k] == 0.0:
                    continue
                price = closes[s][t - DAY]
                sz = size_from_weight(s, abs(w_pre[k]), CAPITAL, price,
                                      filters.get(s, SymbolFilters(s)))
                if sz.ok:
                    w_real[k] = np.sign(w_pre[k]) * sz.notional / CAPITAL

            v = evaluate(w_pre, w_real, mu_mom, GateConfig(), cov, SIGMA_D)
            row["v_ret"] = v.v_ret
            row["coverage"] = v.coverage
            row["n_eff_long"], row["n_eff_short"] = v.n_eff_long, v.n_eff_short
            if not v.passed:
                cat, cause = Calendar.GATE, ",".join(v.failed_gates)
                for gname in v.failed_gates:
                    gate_counts[gname] = gate_counts.get(gname, 0) + 1
                raise StopIteration
            # §60.6.1 S4: execution availability for the sized book TODAY
            missing = [s for k, s in enumerate(symbols)
                       if w_real[k] != 0.0 and t not in execs.get(s, {})]
            if missing:
                cat, cause = Calendar.STRUCTURAL, "missing_exec_bar"
                raise StopIteration
            cat = Calendar.FORMED
        except StopIteration:
            pass
        except IntegrityFailure as e:
            cat, cause = Calendar.STRUCTURAL, f"integrity:{e}"
        except OperationalFailure as e:
            cat, cause = Calendar.OPERATIONAL, f"solver:{e}"
        except DegenerateTarget:
            cat, cause = Calendar.DEGENERATE, "no_trade"
        except Exception as e:                    # §59.11.2 harness class
            cat, cause = Calendar.OPERATIONAL, f"harness:{type(e).__name__}:{e}"

        # ---- transition on the DRIFTED held book (frozen §60.6/§62.3) ----
        held_syms = sorted(held)
        w_h = np.array([held[s] for s in held_syms])
        tr = transition(cat, w_real if cat is Calendar.FORMED else None,
                        w_h if cat is not Calendar.FORMED else
                        np.zeros(0 if w_real is None else len(w_real)),
                        float(np.sum(np.abs(w_h))), consec, g_ref)
        consec = tr.consecutive_nonformed
        g_ref = tr.g_ref_next
        if cat is Calendar.FORMED:
            new_held = {s: float(w_real[k]) for k, s in enumerate(symbols)
                        if w_real[k] != 0.0}
        elif tr.action == "flatten":
            new_held = {}
        else:                                    # hold / rescale (scalar)
            scal = tr.scalar if tr.action == "rescale" else 1.0
            new_held = {s: held[s] * scal for s in held_syms}

        # turnover for the frozen-cost reporting line
        all_syms = set(held) | set(new_held)
        turn = sum(abs(new_held.get(s, 0.0) - held.get(s, 0.0))
                   for s in all_syms)
        turnover_total += turn
        cost_pnl -= turn * ETA

        # ---- price-only forward returns (shadow domain rules apply) ----
        r_sh = r_ac = 0.0
        if t <= last_shadow_day:
            if w_pre is not None and cat in (Calendar.FORMED, Calendar.GATE):
                r_sh = sum(float(w_pre[k]) * (r if (r := _exec_ret(
                    execs, s, t)) is not None else 0.0)
                    for k, s in enumerate(symbols) if w_pre[k] != 0.0)
            for s, w in new_held.items():
                r = _exec_ret(execs, s, t)
                r_ac += w * (r if r is not None else 0.0)
            price_pnl += r_ac
            # realized funding on the held book, (exec t, exec t+1]
            for s, w in new_held.items():
                ts_, rs_ = fund.get(s, ((), ()))
                lo = bisect_right(ts_, exec_time_ms(t))
                hi = bisect_right(ts_, exec_time_ms(t + DAY))
                f_sum = sum(rs_[k] for k in range(lo, hi))
                fund_pnl += -w * f_sum
        shadow.append(r_sh)
        actual.append(r_ac)

        # drift held weights by relative prices (assembly reading §65.2.8)
        drifted = {}
        for s, w in new_held.items():
            r = _exec_ret(execs, s, t) if t <= last_shadow_day else None
            drifted[s] = w * (1.0 + (r if r is not None else 0.0))
        held = drifted

        # carry guard: the frozen OR-rule label, updated ONLY on days a
        # decision was computed — a structural day has no signal to score,
        # and zero-padding it would drag the trailing s_mom artificially
        if mu_mom is not None:
            gres = guard.update(mu_mom, f_hat, w_pre=w_pre)
            s_mom_val, carry_label = gres["s_mom"], gres["label"]
        else:
            s_mom_val, carry_label = None, None
        row.update({"category": _CAT[cat], "cause": cause,
                    "action": tr.action, "s_mom": s_mom_val,
                    "carry_label": carry_label,
                    "gross_pre": 0.0 if w_pre is None else float(
                        np.sum(np.abs(w_pre))),
                    "gross_real": 0.0 if w_real is None else float(
                        np.sum(np.abs(w_real))),
                    "r_shadow": r_sh, "r_actual_price": r_ac})
        daily_rows.append(row)
        calendar.append(cat)
        day_records.append(DayRecord(
            date_ms=t, category=_CAT[cat],
            n_eligible=row.get("n_eligible", 0), capable=capable))
        fh.write(json.dumps(row, default=float) + "\n")
        t += DAY
    fh.close()

    n = len(calendar)
    counts = {c: sum(1 for x in calendar if x is c) for c in Calendar}
    tup = reporting_tuple(calendar, price_pnl / n if n else 0.0, gate_counts)
    dg = delta_gate(np.array(shadow), calendar, "hold+G_ref/M=7 (frozen)")
    dt = delta_transition(np.array(actual), np.array(shadow), calendar,
                          "hold+G_ref/M=7 (frozen)")
    return {"day_records": day_records, "ic_records": ic_records,
            "daily_rows": daily_rows, "reporting_tuple": tup,
            "delta_gate": dg, "delta_transition": dt,
            "counts": {_CAT[c]: v for c, v in counts.items()},
            "price_pnl_full_calendar": price_pnl,
            "funding_pnl_realized": fund_pnl,
            "cost_line_frozen_eta": cost_pnl,
            "turnover_total": turnover_total,
            "n_calib_equivalence_checks": n_calib_checked}
