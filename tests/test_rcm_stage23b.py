"""
Stage 23b: Part 0 (input-C spectrum policy, §63.7.2) and Part II (the two
kill-criterion evaluators, §60.12) — all synthetic, no return read.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm.eval_formation import (  # noqa: E402
    DAY_MS, DayRecord, MIN_NAMES, PASS_MIN_FORMED, evaluate as eval_formation,
    evaluation_start,
)
from rcm.eval_ic import (  # noqa: E402
    N_BOOT, average_ranks, evaluate as eval_ic, seed_from_lock_commit,
    spearman_ic, stationary_bootstrap_ci,
)
from rcm.gates import IntegrityFailure, N_EFF_MIN  # noqa: E402
from rcm.rescov import estimate  # noqa: E402

BASE = 19_000 * DAY_MS


# ------------------------------------------------- Part 0: §63.7.2 policy

def _planted_spectrum(n, lam_min, seed=0):
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.standard_normal((n, n)))
    lam = np.ones(n)
    lam[0], lam[-1] = 3.0, lam_min
    return (q * lam) @ q.T


def test_input_c_materially_negative_fails_closed():
    c = _planted_spectrum(10, -1e-6)
    with pytest.raises(IntegrityFailure, match="non-PSD"):
        estimate(c, np.full(10, 4e-4))
    print("PASS 23b_input_c_fail_closed")


def test_input_c_roundoff_band_proceeds_uncorrected():
    """A GENUINE correlation made numerically non-PSD by −5e-9 along its
    null direction (a duplicated name): λ_min ∈ [−SOLVER_TOL, 0) proceeds,
    and the negative roundoff eigenvalue survives UNCORRECTED — no PSD
    projection, no nearest-correlation repair."""
    rng = np.random.default_rng(1)
    e = rng.standard_normal((10, 90))
    e[1] = e[0]                                  # exact null direction
    c0 = np.corrcoef(e)
    lam0, q0 = np.linalg.eigh(c0)
    c = c0 - 5e-9 * np.outer(q0[:, 0], q0[:, 0])
    lam_min = float(np.linalg.eigvalsh((c + c.T) / 2)[0])
    assert -1e-8 < lam_min < 0, f"fixture must sit in the band ({lam_min})"
    res = estimate(c, np.full(10, 4e-4))
    assert np.all(np.isfinite(res.omega)) and res.k >= 1
    assert float(res.eigenvalues[-1]) < 0, \
        "the negative roundoff eigenvalue must survive UNCORRECTED"
    print(f"PASS 23b_input_c_roundoff (lam_min {res.eigenvalues[-1]:.1e} "
          f"kept, K={res.k})")


# --------------------------------------------- formation evaluator (I.2)

def cal(n_days, categories=None, n_eligible=20, capable=True, start=BASE,
        step=1):
    cats = categories or ["formed"] * n_days
    return [DayRecord(date_ms=start + i * step * DAY_MS, category=cats[i],
                      n_eligible=n_eligible, capable=capable)
            for i in range(n_days)]


def test_formation_boundary_37_fails_38_passes():
    assert PASS_MIN_FORMED == 38
    cats38 = ["formed"] * 38 + ["gate"] * 25
    v = eval_formation(cal(63, cats38))
    assert v.passed and v.n_windows == 1 and v.min_formed_in_any_window == 38
    cats37 = ["formed"] * 37 + ["gate"] * 26
    v = eval_formation(cal(63, cats37))
    assert not v.passed and v.n_failing_windows == 1
    print("PASS 23b_formation_boundary (37 FAIL / 38 PASS)")


def test_formation_evaluation_start_detected_on_the_crossing_date():
    recs = (cal(3, n_eligible=12, capable=False)                 # not capable
            + cal(5, n_eligible=10, start=BASE + 3 * DAY_MS)     # < 12 names
            + cal(70, n_eligible=12, start=BASE + 8 * DAY_MS))
    assert evaluation_start(recs) == BASE + 8 * DAY_MS
    assert MIN_NAMES == 12 == 2 * int(N_EFF_MIN)
    v = eval_formation(recs)
    assert v.evaluation_start_ms == BASE + 8 * DAY_MS
    assert v.n_windows == 70 - 63 + 1
    print("PASS 23b_evaluation_start")


def test_formation_calendar_never_compressed():
    """63 formed records on ALTERNATE days: an index-compressed evaluator
    sees 63 consecutive formed days and passes; the calendar-day evaluator
    must fail (each 63-day window holds only ~32 formed)."""
    recs = cal(63, step=2)                       # days 0, 2, 4, ..., 124
    v = eval_formation(recs)
    assert not v.passed
    assert v.min_formed_in_any_window <= 32
    print(f"PASS 23b_no_compression (min formed "
          f"{v.min_formed_in_any_window}/63 across dead days)")


def test_formation_any_single_failing_window_kills():
    """200 days, one 26-day dead stretch: windows spanning it hold exactly
    37 formed; everything else passes — the criterion must still FAIL."""
    cats = ["formed"] * 200
    for i in range(100, 126):
        cats[i] = "operational"
    v = eval_formation(cal(200, cats))
    assert not v.passed
    assert v.min_formed_in_any_window == 63 - 26 == 37
    assert v.n_failing_windows >= 1 and v.n_windows == 200 - 62
    assert v.first_failing_window_end_ms == BASE + 125 * DAY_MS
    print(f"PASS 23b_any_window ({v.n_failing_windows} failing of "
          f"{v.n_windows})")


def test_formation_all_nonformed_categories_count():
    """D_structural, D_operational, D_degenerate and gate-fail dates all
    count as non-formed — 26 of them across all four categories."""
    cats = (["formed"] * 37 + ["structural"] * 7 + ["operational"] * 7
            + ["degenerate"] * 6 + ["gate"] * 6)
    assert len(cats) == 63
    v = eval_formation(cal(63, cats))
    assert not v.passed and v.min_formed_in_any_window == 37
    with pytest.raises(ValueError, match="unknown calendar category"):
        eval_formation(cal(63, ["vibes"] * 63))
    print("PASS 23b_categories_nonformed")


# --------------------------------------------------- IC evaluator (I.3)

def ic_records(n_dates, n_names, slope, seed, start=BASE):
    rng = np.random.default_rng(seed)
    recs = []
    for t in range(n_dates):
        z = rng.standard_normal(n_names)
        eps = slope * z + rng.standard_normal(n_names)
        recs.append({"date_ms": start + t * DAY_MS, "z_mom": z,
                     "eps_fwd": eps})
    return recs


def test_ic_sign_positive_zero_negative():
    pos = eval_ic(ic_records(300, 30, +0.4, seed=1), seed=7)
    assert pos.passed and pos.ci90[0] > 0
    null = eval_ic(ic_records(300, 30, 0.0, seed=2), seed=7)
    assert not null.passed and null.ci90[0] < 0 < null.ci90[1]
    neg = eval_ic(ic_records(300, 30, -0.4, seed=3), seed=7)
    assert not neg.passed and neg.ci90[1] < 0
    print(f"PASS 23b_ic_sign (+CI [{pos.ci90[0]:+.3f},{pos.ci90[1]:+.3f}], "
          f"0 straddles, − fails)")


def test_ic_equal_weighting_per_date_not_per_pair():
    """40 dates of 200 names with IC = +1 and 40 dates of 12 names with
    IC = −1: the USER estimand gives IC̄ = 0 exactly; an asset-date-pooled
    estimand would be dominated by the large dates."""
    rng = np.random.default_rng(4)
    recs, pooled_z, pooled_e = [], [], []
    for t in range(80):
        n = 200 if t % 2 == 0 else 12
        z = rng.standard_normal(n)
        eps = z.copy() if t % 2 == 0 else -z
        recs.append({"date_ms": BASE + t * DAY_MS, "z_mom": z,
                     "eps_fwd": eps})
        pooled_z.extend(z); pooled_e.extend(eps)
    v = eval_ic(recs, seed=7)
    assert v.ic_mean == 0.0, v.ic_mean
    pooled = spearman_ic(np.array(pooled_z), np.array(pooled_e))[0]
    assert pooled > 0.5, "the rejected pooled estimand would say +"
    print(f"PASS 23b_ic_equal_weight (IC-bar 0.0 vs pooled {pooled:+.3f})")


def test_ic_no_capability_gate_10_name_date_included():
    recs = ic_records(40, 30, +0.4, seed=5)
    recs.append({"date_ms": BASE + 40 * DAY_MS,
                 "z_mom": np.arange(10.0),
                 "eps_fwd": np.arange(10.0) + 0.1})
    v = eval_ic(recs, seed=7)
    assert v.n_defined == 41, "the 10-name date must be INCLUDED"
    print("PASS 23b_ic_no_capability_gate")


def test_ic_undefined_dates_excluded_counted_reasoned():
    recs = ic_records(35, 30, +0.4, seed=6)
    base_mean = eval_ic(recs, seed=7).ic_mean
    recs.append({"date_ms": BASE + 100 * DAY_MS,
                 "z_mom": np.ones(20),                  # constant ranks
                 "eps_fwd": np.random.default_rng(0).standard_normal(20)})
    recs.append({"date_ms": BASE + 101 * DAY_MS,
                 "z_mom": np.array([1.0, 2.0, 3.0]),
                 "eps_fwd": np.array([0.5, np.nan, np.nan])})  # 1 valid pair
    v = eval_ic(recs, seed=7)
    assert v.n_defined == 35 and v.n_excluded == 2
    assert v.exclusion_reasons == {"constant_ranks": 1,
                                   "fewer_than_2_pairs": 1}
    assert v.ic_mean == base_mean, "undefined dates must not shift the mean"
    print(f"PASS 23b_ic_undefined ({v.exclusion_reasons})")


def test_ic_bootstrap_inherits_gen1_code_bit_for_bit():
    """The construction is Gen-1's, not merely similar: with the Sharpe
    statistic substituted, the port reproduces backtest.metrics.
    sharpe_bootstrap_ci EXACTLY, and the cited file hash is pinned."""
    from backtest.metrics import ANN, sharpe_bootstrap_ci
    cited = "061622ed3e786d6dd6e91e5a16c65a4e82634486414d3fc065c0c3f312551328"
    actual = hashlib.sha256(
        (ROOT / "backtest" / "metrics.py").read_bytes()).hexdigest()
    assert actual == cited, ("backtest/metrics.py changed since §60.12 "
                             "cited it — re-cite in the ledger")
    rng = np.random.default_rng(12)
    r = 0.001 + 0.01 * rng.standard_normal(200)

    def sharpe_stat(samples):
        mu = samples.mean(axis=1)
        sd = samples.std(axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(sd > 0, mu / sd * np.sqrt(ANN), np.nan)

    for seed in (0, 17):
        a = sharpe_bootstrap_ci(r, seed=seed)
        b = stationary_bootstrap_ci(r, sharpe_stat, seed=seed)
        assert a == b, "construction diverged from the inherited Gen-1 code"
    # frozen quantities and the n<30 guard travel with the code
    assert N_BOOT == 2000
    ci_short = stationary_bootstrap_ci(np.ones(29), lambda s: s.mean(axis=1),
                                       seed=0)
    assert np.isnan(ci_short[0]) and np.isnan(ci_short[1])
    v = eval_ic(ic_records(100, 20, 0.2, seed=8), seed=7)
    assert v.mean_block == max(2.0, v.n_defined ** (1.0 / 3.0))
    print("PASS 23b_ic_bootstrap_gen1_bit_exact")


def test_ic_seed_rule_deterministic_from_lock_commit():
    h = "4c75295deadbeef0"
    expected = int(hashlib.sha256(h.encode()).hexdigest()[:8], 16)
    assert seed_from_lock_commit(h) == expected
    recs = ic_records(120, 25, 0.2, seed=9)
    a = eval_ic(recs, seed=seed_from_lock_commit(h))
    b = eval_ic(recs, seed=seed_from_lock_commit(h))
    c = eval_ic(recs, seed=seed_from_lock_commit(h) + 1)
    assert a.ci90 == b.ci90 and a.ci90 != c.ci90
    print("PASS 23b_ic_seed_rule")


def test_average_ranks_ties_averaged():
    r = average_ranks(np.array([3.0, 1.0, 3.0, 2.0]))
    assert np.array_equal(r, np.array([3.5, 1.0, 3.5, 2.0]))
    print("PASS 23b_avg_ranks")


# ------------------------------------- both: frozen objects, no readers

def test_evaluators_import_no_reader_and_define_no_second_frozen_object():
    """The evaluators consume the frozen ε_fwd and universe objects — they
    must not rebuild them (single definition), and must reach no data
    reader, no Gen-1 module, and no strategy component."""
    for name in ("eval_formation.py", "eval_ic.py"):
        src = (ROOT / "rcm" / name).read_text(encoding="utf-8")
        imports = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imports |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        project = {i for i in imports if i.startswith(
            ("rcm", "backtest", "live", "research", "xsmom"))}
        assert not project, f"{name} imports {project}"
        for banned in ("residual_series", "orthogonalize", "sqlite",
                       "proddata", "CalibrationSet"):
            assert banned not in src, f"{name} rebuilds/reaches {banned}"
    print("PASS 23b_no_reader_single_definition")


# ------------------------------------------- §64.4 run-stage immutability

def test_lock_hashes_in_notes_match_the_evaluator_files():
    """§64.4: the lock commit pinned the evaluator code. Any post-lock edit
    of either evaluator breaks this test — the run stage may not proceed on
    drifted code without a new pre-registration."""
    import re
    notes = (ROOT / "NOTES.md").read_text(encoding="utf-8")
    locks = dict(re.findall(r"^LOCK (\S+) sha256=([0-9a-f]{64})", notes,
                            re.MULTILINE))
    # §66.5 re-lock (F-2): the formation definition lives partly in
    # rcm/gates.py, so the amended gate is pinned beside the evaluators;
    # dict() keeps the LAST lock line per file — the §66.5 values.
    assert set(locks) == {"rcm/eval_formation.py", "rcm/eval_ic.py",
                          "rcm/gates.py"}, locks
    for rel, cited in locks.items():
        actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert actual == cited, (
            f"{rel} changed after the §64 lock — the pre-registration is "
            f"void; re-register before any run")
    print("PASS 23b_lock_immutability")
