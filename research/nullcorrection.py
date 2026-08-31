"""
Stage 21a (NOTES §63.5): the analytic finite-sample independence null for
the §63.3 residual-correlation measurement.

READS NO RETURNS. The only input is the committed §63.3 record
`residcorr_out/diagnostics.jsonl`; every quantity here is arithmetic on
`N_t` and the constants of the frozen design (T = 90, three regressors).
Import quarantine (AST-tested): json/pathlib/numpy only — no database
driver, no network client, no rcm/backtest/live module of any kind.

The references (§63.5.2, registered before this file existed):

    m = T − 3 = 87
    s_MP+(N)      = (1 + sqrt(N/m))² / N        MP upper-edge SHARE —
                                                asymptotic reference, NOT a
                                                critical value
    F_RMS,null(N) = sqrt(N(N−1)/m)              RMS null scale — sqrt of
                                                E[‖C−I‖_F²], NOT E[‖C−I‖_F]

Per-date ratios R_λ1 and R_F go to the sibling file `null_ratios.jsonl`
(the §63.3 record is never rewritten). D.4 stands: nothing here feeds any
strategy component.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

T_OBS = 90                    # §60.1 frozen window
REG_DIM = 3                   # intercept + f_BTC + f_ETH⊥, frozen design
M_DOF = T_OBS - REG_DIM       # 87
PCTS = (5, 25, 50, 75, 95)    # §63.2.4's summary, reused verbatim

IN_PATH = Path(__file__).resolve().parent / "residcorr_out" / "diagnostics.jsonl"
OUT_PATH = Path(__file__).resolve().parent / "residcorr_out" / "null_ratios.jsonl"


def s_mp_plus(n: int) -> float:
    """Marchenko–Pastur upper-edge eigenvalue SHARE at dimension n, m=87."""
    if n < 1:
        raise ValueError("n must be positive")
    return (1.0 + np.sqrt(n / M_DOF)) ** 2 / n


def f_rms_null(n: int) -> float:
    """√E[‖C−I‖_F²] under the spherical independence null (E[ρ²]=1/m)."""
    if n < 2:
        raise ValueError("needs n >= 2")
    return float(np.sqrt(n * (n - 1) / M_DOF))


def run(in_path: Path = IN_PATH, out_path: Path = OUT_PATH) -> dict:
    """Compute R_λ1, R_F for every defined §63.3 date; write the sibling
    file; return the §63.3-style percentile summary."""
    r_l1, r_f = [], []
    rows_out = []
    n_rows = n_defined = 0
    with in_path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            n_rows += 1
            n = row["N_t"]
            if n < 2 or row["eig1_share"] is None:
                rows_out.append({"t_ms": row["t_ms"], "N_t": n,
                                 "s_mp_plus": None, "f_rms_null": None,
                                 "R_l1": None, "R_F": None})
                continue
            n_defined += 1
            smp = s_mp_plus(n)
            frn = f_rms_null(n)
            rl1 = row["eig1_share"] / smp
            rf = row["frobenius_dist"] / frn
            r_l1.append(rl1)
            r_f.append(rf)
            rows_out.append({"t_ms": row["t_ms"], "N_t": n,
                             "s_mp_plus": smp, "f_rms_null": frn,
                             "R_l1": rl1, "R_F": rf})
    with out_path.open("w", encoding="utf-8") as fh:
        for r in rows_out:
            fh.write(json.dumps(r) + "\n")
    summary = {"n_rows": n_rows, "n_defined": n_defined, "m_dof": M_DOF}
    for name, vals in (("R_l1", r_l1), ("R_F", r_f)):
        v = np.asarray(vals, float)
        summary[name] = {f"p{p}": float(np.percentile(v, p)) for p in PCTS}
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
