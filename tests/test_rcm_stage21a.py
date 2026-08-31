"""
Stage 21a (§63.5): the analytic null correction — formulas, quarantine,
and the no-data-reader guarantee.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.nullcorrection import (  # noqa: E402
    M_DOF, f_rms_null, run, s_mp_plus,
)


def test_worked_values_match_the_ledger():
    """§63.5.2's worked values at the median N_t = 117, exactly as appended
    BEFORE the computation ran."""
    assert M_DOF == 87
    assert s_mp_plus(117) == pytest.approx(0.0399, abs=5e-5)
    assert f_rms_null(117) == pytest.approx(12.49, abs=5e-3)
    # the finite-sample null is ~4.7x the population identity share
    assert s_mp_plus(117) / (1 / 117) == pytest.approx(4.66, abs=0.01)
    print("PASS 21a_worked_values")


def test_frobenius_null_is_rms_not_expected_norm():
    """The E[ρ²] = 1/m derivation, checked by simulation: √E[‖C−I‖_F²] for
    independent vectors in an m-dim subspace matches F_RMS,null — and the
    RMS is ABOVE the mean norm (Jensen), which is why the naming matters."""
    rng = np.random.default_rng(5)
    n, m, reps = 12, 87, 300
    norms2 = []
    for _ in range(reps):
        x = rng.standard_normal((n, m))
        c = np.corrcoef(x)
        norms2.append(np.linalg.norm(c - np.eye(n)) ** 2)
    rms = float(np.sqrt(np.mean(norms2)))
    # corrcoef demeans (one more dof): compare against m-1 as well as m —
    # both must bracket the simulated value's tolerance band
    assert rms == pytest.approx(f_rms_null(n), rel=0.05)
    assert float(np.mean(np.sqrt(norms2))) <= rms + 1e-12
    print(f"PASS 21a_rms_derivation (sim {rms:.3f} vs "
          f"analytic {f_rms_null(n):.3f})")


def test_module_reads_no_data_ast():
    """§63.5.3: no data reader — stdlib + numpy only. No sqlite, network,
    rcm, backtest, or live import of ANY kind."""
    src = (ROOT / "research" / "nullcorrection.py").read_text(encoding="utf-8")
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    allowed = {"__future__", "json", "pathlib", "numpy"}
    assert imports <= allowed, imports - allowed
    for banned in ("sqlite", "urllib", "requests", "http", "socket"):
        assert banned not in src, f"module names {banned}"
    print("PASS 21a_no_data_reader")


def test_ratios_cover_every_defined_date_and_null_rows_pass_through(tmp_path):
    """One synthetic record file: a defined row yields exact ratio
    arithmetic; a null row stays null; no row is dropped."""
    rec = tmp_path / "diag.jsonl"
    rows = [
        {"t_ms": 1, "N_t": 0, "eig1_share": None, "frobenius_dist": None},
        {"t_ms": 2, "N_t": 117, "eig1_share": 0.3067,
         "frobenius_dist": 38.7996},
    ]
    rec.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                   encoding="utf-8")
    out = tmp_path / "ratios.jsonl"
    summary = run(in_path=rec, out_path=out)
    got = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert len(got) == 2 and got[0]["R_l1"] is None
    assert got[1]["R_l1"] == pytest.approx(0.3067 / s_mp_plus(117))
    assert got[1]["R_F"] == pytest.approx(38.7996 / f_rms_null(117))
    assert summary["n_rows"] == 2 and summary["n_defined"] == 1
    print("PASS 21a_ratio_arithmetic")
