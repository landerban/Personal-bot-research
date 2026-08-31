"""
Stage 21 Parts B/C (§63.1): zero-momentum trades labelled with coverage N/A;
the V_ret exposure-retention gate under the optimizer's Σ; the integrity
split; the absolute ceiling; the single 0.40.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm.factors import CovarianceModel  # noqa: E402
from rcm.gates import (  # noqa: E402
    COVERAGE_NA, DegenerateTarget, GateConfig, IntegrityFailure, V_RET_MIN,
    c_signal, evaluate,
)
from rcm.momentum import CARRY_LABEL, CarryGuard  # noqa: E402
from rcm.optimizer import solve  # noqa: E402
from rcm.statemachine import Calendar, classify  # noqa: E402

SIG = 0.10 / np.sqrt(365)


def diag_cov(n, idio=0.02, b_btc=None):
    return CovarianceModel(
        b_btc=np.zeros(n) if b_btc is None else np.asarray(b_btc, float),
        b_eth=np.zeros(n), sf_btc=0.03, sf_eth=0.015,
        d_idio=np.full(n, idio))


def book(n=6):
    return np.array([0.1] * (n // 2) + [-0.1] * (n // 2))


# ------------------------------------------------- Part B: §63.1.A.1 decided

def test_zero_momentum_coverage_is_the_distinct_na_value():
    """USER DECISION (a): not a raise, not a gate failure, and the value is
    distinct from 0, 1, and NaN — arithmetic on it fails loudly."""
    w_pre = book()
    cov = c_signal(w_pre, np.zeros(6), np.ones(6, dtype=bool))
    assert cov is COVERAGE_NA
    assert cov != 0 and cov != 1 and not isinstance(cov, float)
    with pytest.raises(TypeError):
        cov + 1.0  # noqa: B018 — must not silently average into a rate
    print("PASS 21_coverage_na_distinct")


def test_zero_momentum_day_forms_and_is_not_a_gate_failure():
    """The book forms under the §62.8 centered construction and the N/A
    coverage never appears in failed_gates; the day is FORMED (with the
    label), not D_degenerate and not D_gate."""
    n = 20
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    rng = np.random.default_rng(7)
    cov = CovarianceModel(b_btc=1.0 + 0.15 * rng.standard_normal(n),
                          b_eth=0.2 * rng.standard_normal(n),
                          sf_btc=0.03, sf_eth=0.015, d_idio=np.full(n, 0.02))
    se = np.full(n, 0.08)
    f_hat = np.linspace(0.0005, 0.0085, n)     # μ_mom ≡ 0: pure carry
    out = solve(symbols, 0.0 - f_hat, cov, se, se, SIG)
    assert out.gross > 0.3
    v = evaluate(out.w, out.w, np.zeros(n), GateConfig(), cov, SIG)
    assert v.coverage is COVERAGE_NA
    assert "signal_coverage" not in v.failed_gates
    assert v.passed, v.failed_gates
    assert classify(set()) is Calendar.FORMED
    print(f"PASS 21_zero_momentum_forms (gross {out.gross:.3f}, "
          f"coverage {v.coverage})")


def test_carry_label_fires_on_both_classes_and_only_those():
    """Part B label test, corrected: exact-zero-momentum formed days MUST
    carry the label; carry-dominant trailing days also carry it; absence
    only when neither condition holds."""
    # class 1: exact zero momentum mass today (even with healthy history)
    g = CarryGuard()
    for _ in range(21):
        g.update(np.full(4, 1e-3), f_hat=np.full(4, 1e-5))   # s_mom ≈ 1
    r = g.update(np.zeros(4), f_hat=np.full(4, 3e-4))
    assert r["zero_momentum_mass"] and r["label"] == CARRY_LABEL
    # class 1b: w_pre-weighted zero mass with nonzero μ_mom elsewhere
    g2 = CarryGuard()
    r2 = g2.update(np.array([0.0, 0.0, 1e-3, 1e-3]),
                   f_hat=np.full(4, 1e-5),
                   w_pre=np.array([0.2, -0.2, 0.0, 0.0]))
    assert r2["zero_momentum_mass"] and r2["label"] == CARRY_LABEL
    # class 2: §60.2.3 trailing rule, momentum nonzero today
    g3 = CarryGuard()
    for _ in range(21):
        g3.update(np.full(4, 1e-5), f_hat=np.full(4, 1e-3))  # s_mom ≈ 0.01
    r3 = g3.update(np.full(4, 1e-5), f_hat=np.full(4, 1e-3))
    assert not r3["zero_momentum_mass"]
    assert r3["carry_flag"] and r3["label"] == CARRY_LABEL
    # neither: healthy momentum share, nonzero mass — label ABSENT
    g4 = CarryGuard()
    r4 = g4.update(np.full(4, 1e-3), f_hat=np.full(4, 1e-4),
                   w_pre=np.array([0.2, -0.2, 0.1, -0.1]))
    assert r4["label"] is None and not r4["carry_flag"]
    print("PASS 21_carry_label_both_classes")


# ------------------------------------------------- Part C: §63.1.A.2 decided

def test_v_ret_is_exactly_g_squared_under_proportional_scaling():
    """w_real = g·w_pre ⇒ V_ret = g² — bit-exact for dyadic g because
    power-of-two scaling commutes with float rounding."""
    w_pre = book()
    cov = diag_cov(6)
    v = evaluate(w_pre, 0.5 * w_pre, np.ones(6), GateConfig(), cov, SIG)
    assert v.v_ret == 0.25
    assert "exposure_retention" in v.failed_gates          # 0.25 < 0.40
    assert v.g_ratio == 0.5, "gross ratio stays reported, diagnostic only"
    print("PASS 21_vret_proportional_exact")


def test_v_ret_boundary_fires_3999_passes_4001():
    w_pre = book()
    cov = diag_cov(6)
    lo = evaluate(w_pre, np.sqrt(0.3999) * w_pre, np.ones(6), GateConfig(),
                  cov, SIG)
    hi = evaluate(w_pre, np.sqrt(0.4001) * w_pre, np.ones(6), GateConfig(),
                  cov, SIG)
    assert "exposure_retention" in lo.failed_gates
    assert "exposure_retention" not in hi.failed_gates
    assert lo.v_ret == pytest.approx(0.3999, abs=1e-12)
    assert hi.v_ret == pytest.approx(0.4001, abs=1e-12)
    print("PASS 21_vret_boundary")


def test_composition_change_makes_v_ret_diverge_from_g_squared():
    """Dropping a name is not a rescale: the gross ratio and the variance
    ratio must disagree — the reason the gate is V_ret and not G-based."""
    w_pre = book()
    w_real = w_pre.copy()
    w_real[0] = 0.0                                        # one long dropped
    cov = diag_cov(6, b_btc=np.ones(6))
    v = evaluate(w_pre, w_real, np.ones(6), GateConfig(), cov, SIG)
    assert v.v_ret != pytest.approx(v.g_ratio ** 2, rel=1e-3), (
        f"V_ret {v.v_ret:.4f} vs g² {v.g_ratio**2:.4f}: composition change "
        f"must break the proportional identity")
    print(f"PASS 21_vret_composition (V_ret {v.v_ret:.4f} ≠ "
          f"g² {v.g_ratio**2:.4f})")


def test_gate_and_optimizer_share_one_covariance_object():
    """§63.1.A.2.1: one risk model, two uses. The verdict's V_ret must be
    computed by THE object the optimizer solved with — asserted by counting
    calls on that exact instance's method."""
    n = 20
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    rng = np.random.default_rng(11)

    calls = []

    class WitnessCov(CovarianceModel):
        def portfolio_vol(self, w):
            calls.append(id(self))
            return CovarianceModel.portfolio_vol(self, w)

    cov = WitnessCov(b_btc=1.0 + 0.15 * rng.standard_normal(n),
                     b_eth=0.2 * rng.standard_normal(n),
                     sf_btc=0.03, sf_eth=0.015, d_idio=np.full(n, 0.02))
    se = np.full(n, 0.08)
    mu = np.linspace(0.004, -0.004, n)
    out = solve(symbols, mu, cov, se, se, SIG)
    n_before = len(calls)
    v = evaluate(out.w, out.w, mu, GateConfig(), cov, SIG)
    assert len(calls) == n_before + 2, "gate must price w_pre AND w_real"
    assert set(calls) == {id(cov)}, "a second covariance object was used"
    assert v.v_ret == pytest.approx(1.0, abs=1e-12)
    print("PASS 21_one_covariance_object")


def test_absolute_ceiling_rejects_a_risk_regaining_quantization():
    """§63.1.A.2.2: drop the hedging shorts from a beta-one book and modeled
    variance RISES past σ²_target — the executable book must be rejected;
    nothing may exceed the frozen 10% annual target."""
    w_pre = book()                     # neutral, vol 0.0049 < σ_target
    cov = diag_cov(6, b_btc=np.ones(6))
    ok = evaluate(w_pre, w_pre, np.ones(6), GateConfig(), cov, SIG)
    assert "vol_ceiling" not in ok.failed_gates
    w_real = np.where(w_pre > 0, w_pre, 0.0)               # shorts dropped
    v = evaluate(w_pre, w_real, np.ones(6), GateConfig(), cov, SIG)
    assert cov.portfolio_vol(w_real) > SIG, "fixture must actually regain risk"
    assert "vol_ceiling" in v.failed_gates
    assert not v.passed
    print(f"PASS 21_vol_ceiling (regained {cov.portfolio_vol(w_real):.4f} "
          f"> target {SIG:.4f}, rejected)")


def test_zero_denominator_split_degenerate_vs_integrity():
    """w_pre = 0 ⇒ D_degenerate as before; w_pre ≠ 0 in a modeled nullspace
    ⇒ IntegrityFailure, fail closed, D_structural — never an economic zero.
    A throttled micro-book is NOT flagged (the criterion is scale-free)."""
    cfg = GateConfig()
    with pytest.raises(DegenerateTarget, match="G_pre = 0"):
        evaluate(np.zeros(4), np.zeros(4), np.ones(4), cfg, diag_cov(4), SIG)
    null_cov = diag_cov(4, idio=0.0)                       # Σ = 0 exactly
    w = np.array([0.1, 0.1, -0.1, -0.1])
    with pytest.raises(IntegrityFailure, match="singularity"):
        evaluate(w, w, np.ones(4), cfg, null_cov, SIG)
    # the integrity day is classified structural, upstream of everything
    assert classify({"structural_pre", "gates"}) is Calendar.STRUCTURAL
    # scale-free: a 1e-5-gross micro-book under an honest Σ is fine
    micro = 1e-5 * w
    v = evaluate(micro, micro, np.ones(4), cfg, diag_cov(4), SIG)
    assert v.v_ret == pytest.approx(1.0, abs=1e-9)
    # non-finite modeled variance also fails closed
    nan_cov = diag_cov(4, idio=float("nan"))
    with pytest.raises(IntegrityFailure):
        evaluate(w, w, np.ones(4), cfg, nan_cov, SIG)
    print("PASS 21_zero_denominator_split")


def test_the_040_appears_exactly_once_in_rcm():
    """Manifest rule (§63.1.5.5): one definition, cited, grep-tested."""
    hits = []
    for f in sorted((ROOT / "rcm").glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines()):
            if re.search(r"0\.40\b", line):
                hits.append(f"{f.name}:{i + 1}: {line.strip()}")
    assert len(hits) == 1, hits
    assert "V_RET_MIN = 0.40" in hits[0] and "gates.py" in hits[0]
    assert V_RET_MIN == 0.40
    print(f"PASS 21_single_040 ({hits[0].split(':')[0]})")


# --------------------------------------------- Part D: quarantine + refusal

def test_measurement_module_quarantine_ast():
    """§63.2.1: the module may reach factors/seal/universe_filter and NOTHING
    that forms, gates, sizes, or trades. AST-level, not convention."""
    import ast
    src = (ROOT / "research" / "residcorr.py").read_text(encoding="utf-8")
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    banned = {"rcm.optimizer", "rcm.gates", "rcm.statemachine",
              "rcm.attribution", "rcm.momentum", "backtest.engine",
              "backtest.weights", "backtest.runner", "backtest.metrics",
              "backtest.sizing", "live.trader", "live.client", "live.phase2",
              "live.fillsim", "live.proddata"}
    hit = {i for i in imports if i in banned
           or any(i.startswith(b + ".") for b in banned)}
    assert not hit, f"quarantine breach: {hit}"
    allowed_prefixes = ("rcm.factors", "rcm.seal", "backtest.universe_filter")
    project = {i for i in imports
               if i.startswith(("rcm", "backtest", "live", "research", "xsmom"))}
    assert project <= set(allowed_prefixes), project
    # and the inverse: no rcm module may import the research package
    for f in sorted((ROOT / "rcm").glob("*.py")):
        assert "research" not in f.read_text(encoding="utf-8").replace(
            "research plan", "").replace("research order", ""), \
            f"{f.name} references the research package"
    print("PASS 21_quarantine_ast")


def test_measurement_refuses_one_day_past_the_development_window():
    """D.5: 2025-01-01 is refused — by the module's own hard cap, before the
    seal is even consulted, and with no row read."""
    from research.residcorr import END_DAY_MS, DAY_MS, RangeRefused, \
        load_daily_closes, measure_date
    with pytest.raises(RangeRefused, match="hard cap"):
        load_daily_closes(end_ms=END_DAY_MS + DAY_MS)     # 2025-01-01
    with pytest.raises(RangeRefused, match="hard cap"):
        measure_date({}, END_DAY_MS + DAY_MS)
    print("PASS 21_d5_refusal")


def test_measurement_emits_null_rows_never_skips():
    """§63.2.4: a date with no eligible cross-section still emits a row with
    counts and nulls — silent date-dropping is how selection creeps in."""
    from research.residcorr import DAY_MS, measure_date
    t = 1_600_000_000_000 // DAY_MS * DAY_MS              # a 2020 date
    rng = np.random.default_rng(3)
    closes = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        px, series = 100.0, {}
        for k in range(120, 0, -1):
            px *= 1.0 + 0.01 * rng.standard_normal()
            series[t - k * DAY_MS] = px
        closes[sym] = series
    row = measure_date(closes, t)                          # factors only
    assert row["N_t"] == 0 and row["n_class_ok"] == 0
    assert row["offdiag_p50"] is None and row["frobenius_dist"] is None
    assert row["eig1_share"] is None
    print("PASS 21_null_row_discipline")
