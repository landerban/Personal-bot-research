"""
FINDING F-2 (§66): the breadth gate under quantization — the derived
measured-displacement rule. Sub-step rounding must not fail an
exactly-binding book; a genuine drop must still fail it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rcm.factors import CovarianceModel  # noqa: E402
from rcm.gates import GateConfig, evaluate, n_eff  # noqa: E402
from rcm.optimizer import solve  # noqa: E402

SIG = 0.10 / np.sqrt(365)


def _solved_boundary_book():
    """A real optimizer output: the SOC binds at exactly 6 per leg."""
    n = 20
    symbols = sorted(f"A{k:02d}USDT" for k in range(n))
    rng = np.random.default_rng(21)
    cov = CovarianceModel(b_btc=1.0 + 0.15 * rng.standard_normal(n),
                          b_eth=0.2 * rng.standard_normal(n),
                          sf_btc=0.03, sf_eth=0.015, d_idio=np.full(n, 0.02))
    se = np.full(n, 0.08)
    mu = np.linspace(0.004, -0.004, n)
    out = solve(symbols, mu, cov, se, se, SIG)
    return out.w, mu, cov


def _quantize(w_pre, capital=800.0, price=50.0, step=0.001):
    """The frozen §57.3 arithmetic: floor to step, never up."""
    w = np.zeros_like(w_pre)
    for i, x in enumerate(w_pre):
        qty = np.floor(abs(x) * capital / price / step) * step
        w[i] = np.sign(x) * qty * price / capital
    return w


def test_f2_quantized_boundary_book_passes_and_old_rule_would_fail():
    w_pre, mu, cov = _solved_boundary_book()
    w_real = _quantize(w_pre)
    # the fixture must actually reproduce F-2: the sized legs sit BELOW
    # 6 - 1e-6 on rounding alone, with the support fully intact
    nl_sized = n_eff(np.where(w_real > 0, w_real, 0.0))
    ns_sized = n_eff(np.where(w_real < 0, -w_real, 0.0))
    assert np.count_nonzero(w_real) == np.count_nonzero(w_pre), \
        "fixture must not drop names — F-2 is pure rounding"
    assert min(nl_sized, ns_sized) < 6 - 1e-6, (
        f"fixture failed to reproduce F-2 ({nl_sized:.6f}/{ns_sized:.6f})")
    v = evaluate(w_pre, w_real, mu, GateConfig(), cov, SIG)
    assert "n_eff_long" not in v.failed_gates
    assert "n_eff_short" not in v.failed_gates
    assert v.n_eff_long >= 6 - 1e-6 and v.n_eff_short >= 6 - 1e-6
    print(f"PASS f2_rounding_immune (sized legs {nl_sized:.4f}/"
          f"{ns_sized:.4f} below 6; gated breadth "
          f"{v.n_eff_long:.6f}/{v.n_eff_short:.6f})")


def test_f2_genuine_drops_still_fail():
    """Dropping names removes their breadth in full — the gate still bites
    where breadth is really lost."""
    w_pre, mu, cov = _solved_boundary_book()
    w_real = _quantize(w_pre)
    longs = np.where(w_real > 0)[0]
    w_real[longs[:3]] = 0.0                       # three longs under a floor
    v = evaluate(w_pre, w_real, mu, GateConfig(), cov, SIG)
    assert "n_eff_long" in v.failed_gates
    assert v.n_eff_long < 6 - 1e-6
    print(f"PASS f2_drops_fail (long breadth {v.n_eff_long:.3f} after 3 "
          f"drops)")


def test_f2_other_gates_untouched_on_sized_book():
    """V_ret and the ceiling stay on the SIZED book exactly as frozen."""
    w_pre, mu, cov = _solved_boundary_book()
    v = evaluate(w_pre, 0.5 * w_pre, mu, GateConfig(), cov, SIG)
    assert v.v_ret == 0.25 and "exposure_retention" in v.failed_gates
    print("PASS f2_other_gates_unchanged")
