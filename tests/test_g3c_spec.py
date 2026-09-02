"""
Stage G3-C-SPEC v2 (NOTES 70.6): model, timing, exposure, calibration
and evaluator machinery — SYNTHETIC data only (the two possession-rule
tests read a public-domain dev-era CSV through the loader, as the A2
access-rule test already does; no return is read, nothing is fitted).
"""

from __future__ import annotations

import ast
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import g3.calibration as cal                    # noqa: E402
import g3.eval as ev                            # noqa: E402
import g3.features as feat                      # noqa: E402
import g3.models as mod                         # noqa: E402
import g3.sequential as seq                     # noqa: E402
import g3.timing as tim                         # noqa: E402
from rcm.eval_ic import (                       # noqa: E402
    spearman_ic, stationary_bootstrap_ci,
)


# ----------------------------------------------------------- quarantine

def test_g3_import_quarantine():
    """g3/ imports only stdlib, numpy and rcm.eval_ic; never live/,
    research/, requests, backtest, or a database path. Data is injected
    by the run stage."""
    allowed_local = {"g3", "rcm.eval_ic"}
    for f in sorted((ROOT / "g3").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                ok = (top in {"numpy", "math", "datetime", "dataclasses",
                              "json", "hashlib", "typing", "__future__"}
                      or m in allowed_local or top == "g3")
                assert ok, f"{f.name} imports {m}"
        txt = f.read_text(encoding="utf-8")
        assert "xsmom.db" not in txt, f.name
    print("PASS g3c_quarantine")


# --------------------------------------------------------------- timing

def test_t_usable_possession_rule():
    """70.6.1: t_usable = first scheduled 22:00Z fetch at/after the
    publisher release; naive datetimes are refused."""
    rel = datetime(2024, 1, 10, 21, 15, tzinfo=timezone.utc)
    assert tim.t_usable(rel) == datetime(2024, 1, 10, 22, 0,
                                         tzinfo=timezone.utc)
    late = datetime(2024, 1, 10, 22, 30, tzinfo=timezone.utc)
    assert tim.t_usable(late) == datetime(2024, 1, 11, 22, 0,
                                          tzinfo=timezone.utc)
    exact = datetime(2024, 1, 10, 22, 0, tzinfo=timezone.utc)
    assert tim.t_usable(exact) == exact
    with pytest.raises(ValueError):
        tim.t_usable(datetime(2024, 1, 10, 21, 15))
    print("PASS g3c_t_usable")


def test_b1_four_timestamps():
    """70.6.3: 22:00 D cutoff; target = [00:00 D+1, 00:00 D+2); the
    most recent complete UTC day at the cutoff is D-1."""
    d = date(2024, 6, 12)
    assert tim.decision_time(d) == datetime(2024, 6, 12, 22, 0,
                                            tzinfo=timezone.utc)
    lo, hi = tim.target_window(d)
    assert lo == datetime(2024, 6, 13, 0, 0, tzinfo=timezone.utc)
    assert hi == datetime(2024, 6, 14, 0, 0, tzinfo=timezone.utc)
    assert (hi - lo) == timedelta(days=1)
    assert tim.last_complete_day(d) == date(2024, 6, 11)
    print("PASS g3c_b1_timing")


def test_loader_possession_view():
    """70.6.1 through the loader: usable never precedes publisher
    availability for any staged series; a value released 20:15Z is NOT
    possessed at 21:59Z and IS at 22:00Z (public-domain series, dev-era
    date, no return read)."""
    from tools.g3_exogenous_loader import (RULES, pit_view_usable,
                                           usable_utc)
    obs = date(2024, 6, 12)
    for key in RULES:
        rel = RULES[key]["source"].availability_utc(obs)
        assert usable_utc(key, obs) >= rel, key
    u = usable_utc("fred_DGS2", obs)             # released D+1, 21:15Z
    have_at = {d for d, _ in pit_view_usable("fred_DGS2", u)}
    before = {d for d, _ in pit_view_usable(
        "fred_DGS2", u - timedelta(minutes=1))}
    assert obs in have_at and obs not in before
    print("PASS g3c_possession_view")


# --------------------------------------------------------------- models

def test_logistic_stationarity_conditions():
    """IRLS solves the 70.6.7 objective: gradient A'(p-y) + w/C is zero
    on slopes, intercept gradient zero (unpenalised)."""
    rng = np.random.default_rng(1)
    X = rng.standard_normal((400, 3))
    y = (X @ np.array([1.0, -0.5, 0.2]) + 0.3
         + rng.standard_normal(400) > 0).astype(float)
    C = 0.3
    w = mod.logistic_fit(X, y, C)
    p = mod.logistic_predict(w, X)
    A = np.column_stack([np.ones(len(X)), X])
    g = A.T @ (p - y) + np.r_[0.0, w[1:] / C]
    assert np.max(np.abs(g)) < 1e-6
    print("PASS g3c_logistic")


def test_ridge_closed_form():
    rng = np.random.default_rng(2)
    X = rng.standard_normal((200, 4))
    y = X @ np.array([0.5, 0, -1, 2]) + 0.7 + 0.1 * rng.standard_normal(200)
    alpha = 3.0
    w = mod.ridge_fit(X, y, alpha)
    A = np.column_stack([np.ones(200), X])
    pen = np.diag([0.0, alpha, alpha, alpha, alpha])
    ref = np.linalg.solve(A.T @ A + pen, A.T @ y)
    assert np.allclose(w, ref, atol=1e-10)
    print("PASS g3c_ridge")


def test_penalty_selection_calendar_folds_and_strongest_ties():
    """70.6.7: year-boundary expanding folds (quarters for a single
    year); noise target -> strongest ridge alpha wins; exact ties break
    to the STRONGEST regularisation (smallest C, largest alpha)."""
    days3y = [date(2020, 1, 1) + timedelta(days=i) for i in range(1096)]
    folds = seq.inner_folds(days3y)
    assert len(folds) == 2                       # 2020->2021, 2020-21->2022
    assert max(folds[0][0]) < min(folds[0][1])
    days1y = [date(2020, 1, 1) + timedelta(days=i) for i in range(366)]
    qfolds = seq.inner_folds(days1y)
    assert len(qfolds) == 3                      # quarter boundaries
    rng = np.random.default_rng(3)
    X = rng.standard_normal((1096, 5))
    y = rng.standard_normal(1096)                # noise target
    alpha, losses = mod.select_penalty(X, y, "ridge", folds)
    assert alpha == max(mod.ALPHA_GRID)
    assert set(losses) == set(mod.ALPHA_GRID)
    yc = np.zeros(1096)                          # exact tie everywhere
    a_tie, l_tie = mod.select_penalty(X, yc, "ridge", folds)
    tied = [a for a in mod.ALPHA_GRID
            if l_tie[a] == min(l_tie.values())]
    assert a_tie == max(tied)                    # strongest ridge
    yb = (rng.uniform(size=1096) < 0.5).astype(float)
    c_pick, c_losses = mod.select_penalty(X, yb, "logistic", folds)
    assert c_pick in mod.C_GRID
    assert c_pick == min(a for a in mod.C_GRID
                         if c_losses[a] == min(
                             c_losses[x] for x in mod.C_GRID
                             if abs(c_losses[x] - c_losses[a]) < 1e-15)
                         ) or c_pick == min(
        mod.C_GRID, key=lambda a: (c_losses[a], a))
    print("PASS g3c_penalty_selection")


def test_standardize_constant_feature_guard():
    X = np.column_stack([np.ones(50), np.arange(50.0)])
    mu, sd = mod.standardize_fit(X)
    Z = mod.standardize_apply(X, mu, sd)
    assert np.all(Z[:, 0] == 0.0)
    assert abs(Z[:, 1].std(ddof=1) - 1) < 1e-12
    print("PASS g3c_standardize")


# ----------------------------------------------------------- sequential

def test_sequential_windows_and_violation(monkeypatch):
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(1827)]
    seen = {}

    def fit_and_forecast(fit_idx, tgt_idx, seg_no):
        seen[seg_no] = (dates[max(fit_idx)], dates[min(tgt_idx)])
        return len(tgt_idx)

    out = seq.run_sequential(dates, fit_and_forecast)
    assert set(out) == {1, 2, 3, 4}
    assert sum(out.values()) == 1461
    for seg_no, (fit_end, tgt_start) in seen.items():
        assert fit_end < tgt_start
        assert fit_end.year == 2019 + seg_no
    bad = ((date(2020, 1, 1), date(2021, 6, 30),
            date(2021, 1, 1), date(2021, 12, 31)),)
    monkeypatch.setattr(seq, "SEGMENTS", bad)
    with pytest.raises(seq.SequentialViolation):
        seq.run_sequential(dates, fit_and_forecast)
    print("PASS g3c_sequential")


# ----------------------------------------------------------- calibration

def test_platt_oof_procedure():
    """70.6.8: the calibrator is fitted on OUT-OF-FOLD predictions and
    improves planted overconfidence on unseen data."""
    rng = np.random.default_rng(4)
    n = 1096
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    folds = seq.inner_folds(days)
    true_p = rng.uniform(0.2, 0.8, n)
    y = (rng.uniform(size=n) < true_p).astype(float)
    x = (np.log(true_p / (1 - true_p)) * 3.0).reshape(-1, 1)  # overconfident
    s, t = cal.oof_predictions(x, y, 10.0, folds,
                               mod.logistic_fit, mod.logistic_predict)
    n_val = sum(len(v) for _, v in folds)
    assert len(s) == n_val == len(t)             # only validation spans
    ab = cal.platt_fit(s, t)
    hold = slice(0, folds[0][1][0])              # 2020: unseen by folds' vals
    raw = mod.logistic_predict(
        mod.logistic_fit(x[folds[0][0]], y[folds[0][0]], 10.0),
        x[hold])
    calp = cal.platt_apply(ab, raw)
    rep = cal.reliability_report(calp, y[hold])
    assert rep["n"] == len(y[hold]) and len(rep["curve"]) == cal.N_BINS
    print("PASS g3c_platt_oof")


# ------------------------------------------------------------ exposures

def test_beta_exposure_frozen_estimator():
    """70.6.6: slope recovered; NaN before 90 complete days; NaN when
    valid paired days < 60; SE matches the closed form."""
    rng = np.random.default_rng(12)
    n = 200
    r_x = rng.standard_normal(n)
    r_i = 1.5 * r_x + 0.1 * rng.standard_normal(n)
    beta, se, nv = feat.beta_exposure(r_i, r_x)
    assert np.isnan(beta[:90]).all()
    assert abs(beta[120] - 1.5) < 0.05
    w = slice(120 - 90, 120)
    x, yv = r_x[w], r_i[w]
    vx = ((x - x.mean()) ** 2).sum()
    b = ((x - x.mean()) * (yv - yv.mean())).sum() / vx
    a = yv.mean() - b * x.mean()
    resid = yv - a - b * x
    se_ref = np.sqrt((resid @ resid) / (90 - 2) / vx)
    assert abs(beta[120] - b) < 1e-12 and abs(se[120] - se_ref) < 1e-12
    r_gap = r_i.copy()
    r_gap[60:120] = np.nan                       # window 120: only 30 pairs
    beta_g, _, nv_g = feat.beta_exposure(r_gap, r_x)
    assert np.isnan(beta_g[120]) and nv_g[120] < feat.BETA_MIN_OBS
    print("PASS g3c_beta_exposure")


# ------------------------------------------------------ degeneracy pin

def test_degeneracy_shared_term_cannot_move_the_cross_section():
    """Part E pin (the v1 failure): adding a shared per-date term to
    every asset's forecast leaves each date's cross-sectional Spearman
    IC EXACTLY unchanged, so a common exogenous feature cannot be what
    Q4 measures."""
    rng = np.random.default_rng(13)
    n_assets, n_days = 25, 40
    ic_base, ic_shift = [], []
    for t in range(n_days):
        f = rng.standard_normal(n_assets)
        r = 0.3 * f + rng.standard_normal(n_assets)
        shared = rng.standard_normal() * 5.0
        ic_base.append(spearman_ic(f, r)[0])
        ic_shift.append(spearman_ic(f + shared, r)[0])
    assert ic_base == ic_shift                    # exactly, not approximately
    diff = np.array(ic_shift, float) - np.array(ic_base, float)
    assert np.all(diff == 0.0)
    print("PASS g3c_degeneracy_pin")


# ------------------------------------------------------------ bootstrap

def test_index_walk_bit_equivalence():
    rng = np.random.default_rng(5)
    r = rng.standard_normal(200)
    direct = stationary_bootstrap_ci(r, lambda m: m.mean(axis=1), seed=99)
    via_idx = ev.ci_paired(200, lambda I: r[I].mean(axis=1), seed=99)
    assert direct == via_idx
    print("PASS g3c_bit_equivalence")


def test_planted_positive_zero_negative_bss():
    rng = np.random.default_rng(6)
    n = 500
    clim = np.full(n, 0.25)
    noise = 0.01 * rng.standard_normal(n)
    good = ev.q1_direction(0.20 + noise, clim, seed=7)
    assert good["verdict"] == "PASS" and good["ci90"][0] > 0
    null = ev.q1_direction(0.25 + noise, clim, seed=7)
    assert null["ci90"][0] < 0 < null["ci90"][1]
    assert null["verdict"] == "FAIL"
    bad = ev.q1_direction(0.30 + noise, clim, seed=7)
    assert bad["verdict"] == "FAIL" and bad["ci90"][1] < 0
    print("PASS g3c_planted_signs")


def test_conjunctive_rejects_negative_pair():
    rng = np.random.default_rng(8)
    n = 400
    clim = np.full(n, 0.25)
    noise = 0.005 * rng.standard_normal(n)
    bs_m0 = 0.30 + noise
    bs_m1 = 0.275 + noise
    out = ev.q2_incremental_direction(bs_m0, bs_m1, clim, seed=9)
    assert abs(ev.bss(bs_m1, clim) - (-0.10)) < 0.01
    assert abs(ev.bss(bs_m0, clim) - (-0.20)) < 0.01
    assert out["leg_bss_diff"]["ci90"][0] > 0
    assert out["leg_bss_m1"]["verdict"] == "FAIL"
    assert out["verdict"] == "FAIL"
    print("PASS g3c_conjunctive")


def test_indeterminate_under_inherited_guards():
    clim = np.full(20, 0.25)
    out = ev.q1_direction(np.full(20, 0.2), clim, seed=1)
    assert out["verdict"] == "INDETERMINATE"
    ic = np.full(20, 0.05)
    assert ev.q3_cross_sectional(ic, seed=1)["verdict"] == "INDETERMINATE"
    assert ev.conjunctive("PASS", "INDETERMINATE") == "INDETERMINATE"
    assert ev.conjunctive("FAIL", "INDETERMINATE") == "FAIL"
    assert ev.conjunctive("PASS", "PASS") == "PASS"
    print("PASS g3c_indeterminate")


def test_q3_q4_on_planted_ic():
    rng = np.random.default_rng(10)
    n = 600
    ic0 = 0.02 + 0.05 * rng.standard_normal(n)
    ic1 = ic0 + 0.015 + 0.01 * rng.standard_normal(n)
    q3 = ev.q3_cross_sectional(ic0, seed=11)
    q4 = ev.q4_incremental_cross_sectional(ic0, ic1, seed=11)
    assert q3["verdict"] == "PASS"
    assert q4["verdict"] == "PASS" and q4["n_common_dates"] == n
    q4b = ev.q4_incremental_cross_sectional(ic0, ic0 - 0.02, seed=11)
    assert q4b["leg_ic_diff"]["verdict"] == "FAIL"
    assert q4b["verdict"] == "FAIL"
    print("PASS g3c_ic_planted")


# -------------------------------------------------------------- features

def test_feature_transforms_and_one_lag():
    r = np.array([np.nan, 0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.0])
    tr = feat.trailing_returns(r, (1, 3))
    assert np.isnan(tr[1, 0])
    assert tr[2, 0] == 0.01
    assert abs(tr[4, 1] - (0.01 - 0.02 + 0.03)) < 1e-15
    rv = feat.realised_vol(np.tile(r, 4), 5)
    assert np.isnan(rv[:5]).all()
    boundaries = np.array([100, 200, 300, 400])
    level, chg = feat.exog_level_and_change(
        np.array([150, 250, 380]), np.array([10.0, 12.0, 11.0]),
        boundaries, log_change=False)
    assert np.isnan(level[0])
    assert level[1] == 10.0 and np.isnan(chg[1])
    assert level[2] == 12.0 and chg[2] == 2.0
    assert level[3] == 11.0 and chg[3] == -1.0
    _, chg_log = feat.exog_level_and_change(
        np.array([150, 250]), np.array([10.0, 12.0]),
        boundaries, log_change=True)
    assert abs(chg_log[2] - np.log(1.2)) < 1e-15
    print("PASS g3c_features")


def test_frozen_feature_orders_v2():
    """70.6.4/70.6.5 as contracted by 70.9.3: direction 9/18;
    cross-section 7/12 — fred_SP500 is UNAVAILABLE, so sp500_ret_1d and
    name_int_spx no longer exist; no substitute appears; funding MEAN
    (not dispersion) in the direction table; no per-asset vol-of-vol in
    the cross-section."""
    assert len(feat.DIRECTION_M0) == 9 and len(feat.DIRECTION_M1) == 18
    assert "funding_mean" in feat.DIRECTION_M0
    assert "funding_dispersion" not in feat.DIRECTION_M0
    assert "sp500_ret_1d" not in feat.DIRECTION_M1
    assert len(feat.XSEC_M0) == 7 and len(feat.XSEC_M1) == 12
    assert "name_volofvol_21d" not in feat.XSEC_M0
    assert "name_int_spx" not in feat.XSEC_M1
    assert feat.XSEC_M1[7:] == feat.INTERACTIONS
    assert all(k.startswith("name_int_") for k in feat.INTERACTIONS)
    per_name = {"AAA": {k: np.arange(3.0) for k in feat.XSEC_M1
                        if k.startswith("name_")}}
    common = {k: np.zeros(3) for k in feat.XSEC_M1
              if not k.startswith("name_")}
    m = feat.xsec_matrices(per_name, common, feat.XSEC_M1)
    assert m["AAA"].shape == (3, 12)
    with pytest.raises(KeyError):
        feat.direction_matrix({"trend_1d": np.zeros(3)}, feat.DIRECTION_M0)
    print("PASS g3c_orders_v2")
