# RCM (Generation 2) — status

_One page. `NOTES.md` §59–§62 is the authority; if they disagree, NOTES wins._

## Where it stands

**Synthetic-only. Zero trials spent (0 of 20). No real data has touched any
RCM code path** — an import-level test enforces it. The math spec is frozen
(§60 + §60.11 corrections); the machine is built and attacked (219 passed,
no xfails); the seal on 2025-01→2026-07 is enforced in code with a
committed-ledger-entry unlock and a no-back-dating check.

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

## Blocking real data (trial 1 of 20)

| item | status |
|---|---|
| `g_min` (§60.11.6) | OPEN — user route (a)/(b) |
| zero-momentum semantics (§60.11.8) | OPEN — user; delegate recommends (a) |
| correlated-residual stress | UNRESOLVED (§62.5 — the §61.5 chain was not approved; no invariant-derived replacement exists yet) |
| finding F-1 (§61.2) | RESOLVED (§62.8.3) — by construction + projection, no frozen quantity changed |
| solver pin | DONE — clarabel 0.11.1 / cvxpy 1.9.2 |
| determinism, funding observability | DONE |

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
