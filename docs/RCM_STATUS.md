# RCM (Generation 2) — status

_One page. `NOTES.md` §59–§63 is the authority; if they disagree, NOTES wins._

## Where it stands

**Zero trials spent (0 of 20). No real data has touched any `rcm/` code
path** — an import-level test enforces it. Stage 21 recorded the user's
risk preferences (§63.1), implemented the two decidable ones, and ran the
single ledger-authorized development-era STRUCTURE measurement (§63.2–3:
residual correlations 2020–2024, protocol frozen before reading, module
quarantined outside `rcm/`, no performance quantity anywhere). The math
spec is frozen (§60 + §60.11 + §62.8 + §63.1); the machine is built and
attacked (232 passed, no xfails); the seal on 2025-01→2026-07 is enforced
in code with a committed-ledger-entry unlock and a no-back-dating check.

## F-1: proven, resolved by construction, then made shift-invariant — RESOLVED

The incompatibility was proven by a same-gross witness (broad book feasible
at the exact rejected gross, all six instances), then resolved by ADOPTING
the sign-pre-assigned construction with exact per-leg SOC breadth — the
simplest candidate admissible under §62.2 (coefficient-free, mathematically
exact on the net portfolio; the split-variable form was rejected for
fundamental padding). Stage 19c (§62.8) then caught the adopted form's own
bug — raw-sign membership is not invariant to a common forecast shift, which
the dollar-neutral objective cannot see (a +0.006 shift collapsed a
0.90-gross book to zero, recorded) — and corrected it by the unique
projection the neutrality constraint itself implies: membership by
`μ̃ = P·μ_total`, mean computed once, never recomputed. Verified to machine
precision both shift signs with nonzero `w_prev`; the carry-regime book now
forms (semantics still escalated); every `D_degenerate` day records its
cause (`no_trade` vs `constraint_interaction:<binding>`). **F-1 is RESOLVED
(§62.8.3)** — closed unless another synthetic invariant breaks. Stated cost,
measured: when neutrality needs a name held against its signal, the book
shrinks toward D_degenerate. Also adopted: the G_ref downscale-only
stale-book invariant (§62.3) and D_degenerate as a fifth calendar category
(§62.4, broadened §62.8.4). All 219 tests green, zero xfails.

## Blocking trial 1 of 20 — exactly one chain left (§63.4)

| item | status |
|---|---|
| `g_min` (§60.11.6) | RESOLVED — replaced by `V_ret ≥ 0.40` under the optimizer's Σ (§63.1.A.2, user preference; implemented + tested) |
| zero-momentum semantics (§60.11.8) | RESOLVED — user decision (a): trades, labelled `CARRY REGIME — NOT RCM`, coverage `"N/A"` (§63.1.A.1; implemented + tested) |
| correlated-residual stress | DELEGATED (§63.1.A.3) — measurement delivered (§63.3); awaiting the delegates' frozen fixture + criteria, then a later stage executes it |
| finding F-1 (§61.2) | RESOLVED (§62.8.3) — by construction + projection, no frozen quantity changed |
| solver pin | DONE — clarabel 0.11.1 / cvxpy 1.9.2 |
| determinism, funding observability | DONE |

## The Stage-21 measurement packet (for the delegates)

`research/residcorr_out/diagnostics.jsonl` — 1,827 daily rows, 1,642 with
a defined matrix. Headline aggregates (§63.3): daily median off-diagonal
residual correlation has median 0.255 across dates (p5–p95: 0.18–0.38);
λ₁ share median 0.307 vs diagonal expectation ≈ 0.0085 at the median
N_t = 117. D.4 prohibition stands: no strategy component may run under the
measured covariance before the fixture is frozen.

## Module map

```
rcm/seal.py         the structural seal (+ unlock via committed ledger entry)
rcm/timeline.py     one holding interval for everything (§60.4/§60.11.2)
rcm/factors.py      ETH⊥, OLS betas, V_i, factor covariance (one 90d window)
rcm/momentum.py     score, PIT calibration set-builder, shrinkage, carry guard
rcm/funding.py      cadence-aware F̂ via Gen-1 machinery; equality observability
rcm/optimizer.py    SOCP (γ and dollar-tolerance eliminated; η = the cost)
rcm/gates.py        N_eff/leg, bounded C_signal (S=|μ_mom|), symbolic g_min
rcm/statemachine.py causal calendar classifier + hold/rescale/flatten(M=7)
rcm/attribution.py  Δ_gate, Δ_transition, reporting tuple, the literal label
```

Gen-1 (XSMOM) remains frozen and complete at 15 of 25 trials. Holdout sealed
under two independent seals — one of them now executable.
