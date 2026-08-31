"""
Stage 22 Part I.2 (NOTES §63.6): the delegates' random-orientation design
diagnostic, reproduced in-repo with its seed fixed — NOT execution of the
withdrawn stress test, and NOT a pass/fail gate of anything.

Provenance requirements (§63.6 / STAGE22 I.2), implemented verbatim:
  - numpy.random.default_rng(0); 4,000 draws; N = 117
  - total-trace shares (0.3067, 0.0445, 0.0368) from the §63.3 medians
  - D = I (unit residual variances)
  - books: random standard-normal vectors, demeaned (dollar-neutral) and
    unit-normalized — NOT the six F-1 books
  - modes: three standard-normal vectors, demeaned, QR-orthonormalized —
    the complement of span{1} ONLY (betas omitted, an acknowledged
    simplification)
  - C = sum_k lambda_k q_k q_k^T + diag(1 − diag(·)); statistic w^T C w
    against w^T D w = 1
  - draw order per iteration: BOOK FIRST, then modes. The delegates'
    prose did not record this; it was recovered by matching their quoted
    values exactly (book-first: mean 1.010, median 0.861, std 0.456,
    P>1 = 0.344 -> all quoted digits; modes-first gives 0.989/0.855/
    0.416/0.322 and does not match). Recorded as recovered provenance.

The optional informational extension (STAGE22 I.2 "may") runs the same
statistic on the ACTUAL six F-1 books (linear/steep x seeds 21/22/23,
solved under the diagonal model) with modes drawn in the full
span{1, b_btc, b_eth} orthogonal complement — so the ledger's record shows
what the simplified diagnostic did NOT measure. Synthetic only; no real
data anywhere.
"""

from __future__ import annotations

import json

import numpy as np

N_DIAG = 117
SHARES = (0.3067, 0.0445, 0.0368)      # §63.3 medians, total-trace shares
N_DRAWS = 4000


def build_c(modes: np.ndarray, lam: np.ndarray) -> np.ndarray:
    low = (modes * lam) @ modes.T
    return low + np.diag(1.0 - np.diag(low))


def _orth_complement_modes(rng, n: int, span: np.ndarray) -> np.ndarray:
    """Three standard-normal vectors projected off `span`'s columns, then
    QR-orthonormalized. `span` columns need not be orthonormal."""
    q_span, _ = np.linalg.qr(span)
    v = rng.standard_normal((n, 3))
    v -= q_span @ (q_span.T @ v)
    q, _ = np.linalg.qr(v)
    return q


def main_diagnostic() -> dict:
    """The delegates' computation, seed and simplifications exact."""
    rng = np.random.default_rng(0)
    n = N_DIAG
    lam = np.array(SHARES) * n
    ones = np.ones((n, 1))
    ratios = np.empty(N_DRAWS)
    for i in range(N_DRAWS):
        w = rng.standard_normal(n)            # book first (recovered order)
        w -= w.mean()
        w /= np.linalg.norm(w)
        modes = _orth_complement_modes(rng, n, ones)
        ratios[i] = float(w @ build_c(modes, lam) @ w)   # w^T D w = 1
    p_pass_six = (1.0 - float(np.mean(ratios > 1.0))) ** 6
    return {"mean": float(ratios.mean()), "median": float(np.median(ratios)),
            "std": float(ratios.std()), "p_gt_1": float(np.mean(ratios > 1.0)),
            "p_all_six_pass_independence_assumption": p_pass_six}


def f1_books_extension() -> list[dict]:
    """Informational: the six F-1 books under the full span{1, b} complement.
    No criterion attaches to these numbers."""
    from rcm.factors import CovarianceModel
    from rcm.optimizer import solve

    sig = 0.10 / np.sqrt(365)
    out = []
    for seed in (21, 22, 23):
        rng = np.random.default_rng(seed)
        n = 20
        symbols = sorted(f"A{k:02d}USDT" for k in range(n))
        cov = CovarianceModel(b_btc=1.0 + 0.15 * rng.standard_normal(n),
                              b_eth=0.2 * rng.standard_normal(n),
                              sf_btc=0.03, sf_eth=0.015,
                              d_idio=np.full(n, 0.02))
        se = np.full(n, 0.08)
        for shape in ("linear", "steep"):
            if shape == "linear":
                mu = np.linspace(0.004, -0.004, n)
            else:
                base = np.linspace(1, -1, n)
                mu = np.sign(base) * base ** 2 * 0.004
            w = solve(symbols, mu, cov, se, se, sig).w
            wn = w / np.linalg.norm(w)
            span = np.column_stack([np.ones(n), cov.b_btc, cov.b_eth])
            lam = np.array(SHARES) * n
            rng_m = np.random.default_rng(1000 + seed)
            ratios = np.empty(N_DRAWS)
            for i in range(N_DRAWS):
                modes = _orth_complement_modes(rng_m, n, span)
                ratios[i] = float(wn @ build_c(modes, lam) @ wn)
            out.append({"seed": seed, "shape": shape,
                        "mean": float(ratios.mean()),
                        "median": float(np.median(ratios)),
                        "p_gt_1": float(np.mean(ratios > 1.0))})
    return out


if __name__ == "__main__":
    print(json.dumps({"main": main_diagnostic(),
                      "f1_extension": f1_books_extension()}, indent=2))
