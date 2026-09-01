"""
Stage G3-C-SPEC (NOTES 70.2/70.3/70.4): model, calibration, sequential
and evaluator machinery — SYNTHETIC data only; no development return is
read anywhere in this file.
"""

from __future__ import annotations

import ast
import sys
from datetime import date, timedelta
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
from rcm.eval_ic import stationary_bootstrap_ci  # noqa: E402


# ----------------------------------------------------------- quarantine

def test_g3_import_quarantine():
    """70.4/g3 doc: g3/ imports only stdlib, numpy, rcm.eval_ic and
    backtest.costs; never live/, research/, requests, or a database
    path. Data is injected by the run stage."""
    allowed_local = {"g3", "rcm.eval_ic", "backtest.costs"}
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


# --------------------------------------------------------------- models

def test_logistic_stationarity_conditions():
    """IRLS solution satisfies the penalised score equations: X'(p-y) +
    lam*w = 0 on slopes, intercept gradient 0 (unpenalised)."""
    rng = np.random.default_rng(1)
    X = rng.standard_normal((400, 3))
    y = (X @ np.array([1.0, -0.5, 0.2]) + 0.3
         + rng.standard_normal(400) > 0).astype(float)
    lam = 2.5
    w = mod.logistic_fit(X, y, lam)
    p = mod.logistic_predict(w, X)
    A = np.column_stack([np.ones(len(X)), X])
    g = A.T @ (p - y) + np.r_[0.0, lam * w[1:]]
    assert np.max(np.abs(g)) < 1e-6
    print("PASS g3c_logistic")


def test_ridge_closed_form():
    rng = np.random.default_rng(2)
    X = rng.standard_normal((200, 4))
    y = X @ np.array([0.5, 0, -1, 2]) + 0.7 + 0.1 * rng.standard_normal(200)
    lam = 3.0
    w = mod.ridge_fit(X, y, lam)
    A = np.column_stack([np.ones(200), X])
    pen = np.diag([0.0, lam, lam, lam, lam])
    ref = np.linalg.solve(A.T @ A + pen, A.T @ y)
    assert np.allclose(w, ref, atol=1e-10)
    print("PASS g3c_ridge")


def test_lambda_selection_frozen_folds_and_ties():
    """Pure-noise target -> heaviest regularisation wins; the tie rule
    (<= while ascending) selects the LARGER lambda on exact ties."""
    rng = np.random.default_rng(3)
    X = rng.standard_normal((500, 5))
    y = rng.standard_normal(500)                        # noise target
    lam, losses = mod.select_lambda(X, y, "ridge")
    assert lam == max(mod.LAMBDA_GRID)
    assert set(losses) == set(mod.LAMBDA_GRID)
    # planted exact tie: constant target -> all lambdas identical loss
    yc = np.zeros(500)
    lam_tie, losses_tie = mod.select_lambda(X, yc, "ridge")
    tied = [l_ for l_ in mod.LAMBDA_GRID
            if losses_tie[l_] == min(losses_tie.values())]
    assert lam_tie == max(tied)
    print("PASS g3c_lambda_selection")


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
    assert sum(out.values()) == 1461               # 70.2.3 OOS days
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

def test_platt_improves_planted_miscalibration():
    rng = np.random.default_rng(4)
    true_p = rng.uniform(0.2, 0.8, 3000)
    y = (rng.uniform(size=3000) < true_p).astype(float)
    # overconfident scores: push toward 0/1
    z = np.log(true_p / (1 - true_p)) * 3.0
    p_over = 1 / (1 + np.exp(-z))
    fit, held = slice(0, 2400), slice(2400, 3000)
    ab = cal.platt_fit(p_over[fit], y[fit])
    p_cal = cal.platt_apply(ab, p_over[held])
    assert mod.log_loss(p_cal, y[held]) < mod.log_loss(p_over[held], y[held])
    rep = cal.reliability_report(p_cal, y[held])
    assert rep["n"] == 600 and len(rep["curve"]) == cal.N_BINS
    brier = float(ev.brier_series(p_cal, y[held]).mean())
    assert abs(rep["brier_binned"] - brier) < 0.02   # binned identity
    print("PASS g3c_platt")


# ------------------------------------------------------------ bootstrap

def test_index_walk_bit_equivalence():
    """70.3.2: handing the walker the day-index series and computing the
    statistic through the indices reproduces the direct call exactly."""
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
    assert null["verdict"] == "FAIL"                # CI straddles zero
    bad = ev.q1_direction(0.30 + noise, clim, seed=7)
    assert bad["verdict"] == "FAIL" and bad["ci90"][1] < 0
    print("PASS g3c_planted_signs")


def test_conjunctive_rejects_negative_pair():
    """70.4: BSS_M0 = -0.20, BSS_M1 = -0.10 must FAIL Q2 even though the
    difference leg alone is positive."""
    rng = np.random.default_rng(8)
    n = 400
    clim = np.full(n, 0.25)
    noise = 0.005 * rng.standard_normal(n)
    bs_m0 = 0.30 + noise
    bs_m1 = 0.275 + noise
    out = ev.q2_incremental_direction(bs_m0, bs_m1, clim, seed=9)
    assert abs(ev.bss(bs_m1, clim) - (-0.10)) < 0.01
    assert abs(ev.bss(bs_m0, clim) - (-0.20)) < 0.01
    assert out["leg_bss_diff"]["ci90"][0] > 0       # diff leg passes...
    assert out["leg_bss_m1"]["verdict"] == "FAIL"   # ...first leg fails
    assert out["verdict"] == "FAIL"                 # conjunction rejects
    print("PASS g3c_conjunctive")


def test_indeterminate_under_inherited_guards():
    clim = np.full(20, 0.25)
    out = ev.q1_direction(np.full(20, 0.2), clim, seed=1)
    assert out["verdict"] == "INDETERMINATE"        # n < 30 guard
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
    ic1_bad = ic0 - 0.02
    q4b = ev.q4_incremental_cross_sectional(ic0, ic1_bad, seed=11)
    assert q4b["leg_ic_diff"]["verdict"] == "FAIL"
    assert q4b["verdict"] == "FAIL"
    print("PASS g3c_ic_planted")


# -------------------------------------------------------------- features

def test_feature_transforms_and_one_lag():
    r = np.array([np.nan, 0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.0])
    tr = feat.trailing_returns(r, (1, 3))
    assert np.isnan(tr[1, 0])                       # needs r[0]
    assert tr[2, 0] == 0.01                         # r[1], knowable at t=2
    assert abs(tr[4, 1] - (0.01 - 0.02 + 0.03)) < 1e-15
    rv = feat.realised_vol(np.tile(r, 4), 5)
    assert np.isnan(rv[:5]).all()
    boundaries = np.array([100, 200, 300, 400])
    level, chg = feat.exog_level_and_change(
        np.array([150, 250, 380]), np.array([10.0, 12.0, 11.0]),
        boundaries, log_change=False)
    assert np.isnan(level[0])                       # nothing available yet
    assert level[1] == 10.0 and np.isnan(chg[1])    # one obs: no change
    assert level[2] == 12.0 and chg[2] == 2.0       # two most recent
    assert level[3] == 11.0 and chg[3] == -1.0
    _, chg_log = feat.exog_level_and_change(
        np.array([150, 250]), np.array([10.0, 12.0]),
        boundaries, log_change=True)
    assert abs(chg_log[2] - np.log(1.2)) < 1e-15
    print("PASS g3c_features")


def test_frozen_feature_orders():
    assert len(feat.DIRECTION_M0) == 9 and len(feat.DIRECTION_M1) == 19
    assert len(feat.XSEC_M0) == 9 and len(feat.XSEC_M1) == 19
    assert feat.DIRECTION_M1[:9] == feat.DIRECTION_M0
    assert feat.XSEC_M1[9:] == feat.CROSS_ASSET
    per_name = {"AAA": {k: np.arange(3.0) for k in feat.XSEC_M0
                        if k.startswith("name_")}}
    common = {k: np.zeros(3) for k in feat.XSEC_M1
              if not k.startswith("name_")}
    m = feat.xsec_matrices(per_name, common, feat.XSEC_M1)
    assert m["AAA"].shape == (3, 19)
    with pytest.raises(KeyError):
        feat.direction_matrix({"trend_1d": np.zeros(3)}, feat.DIRECTION_M0)
    print("PASS g3c_orders")
