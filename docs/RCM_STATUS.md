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

## Trial-1 prerequisites — every row RESOLVED (§63.7.1)

| item | status |
|---|---|
| `g_min` (§60.11.6) | RESOLVED — replaced by `V_ret ≥ 0.40` under Σ_model (§63.1.A.2, user preference; implemented + tested) |
| zero-momentum semantics (§60.11.8) | RESOLVED — user decision (a): trades, labelled `CARRY REGIME — NOT RCM`, coverage `"N/A"` (§63.1.A.1; implemented + tested) |
| residual-risk model | RESOLVED — delegates withdrew the random-orientation stress gate as unable to render a verdict (§63.6.2) and replaced the diagonal model with the MP-edge residual factor covariance `Ω̂` (§63.6.4), implemented in `rcm/rescov.py` and verified synthetically (§63.7) |
| finding F-1 (§61.2) | RESOLVED (§62.8.3) — construction invariants re-verified under `Ω̂` (§63.7) |
| solver pin | DONE — clarabel 0.11.1 / cvxpy 1.9.2 |
| determinism, funding observability | DONE |

**Trial 1 of 20 is PRE-REGISTERED and LOCKED (§64, Stage 23b) — not
run.** The two §60.8 kill criteria are completed by §60.12 (three user
decisions recorded verbatim; criterion 3 reserved for forward
validation), the two evaluators are built and synthetically attacked
(37/38 boundary, calendar never compressed, equal-weighted daily
Spearman, Gen-1's CI construction inherited bit-for-bit), the evaluator
hashes are pinned in §64.4 with a run-stage immutability test, and
`trials.jsonl` carries the row (`attempt_id 1`, `valid_trial_count 0`).
The reading table has four rows and no post-result discretion.
**STOPPED for both delegates' review of §64; the run is a separate
stage.** Named limitations that travel with the spec (not blockers,
§63.6.5): estimation uncertainty in `K_t`/eigenvalues/loadings; the
`m_eff < 87` caveat; the development-informed naming (§63.1.A.3.1).

## The Stage-21 measurement packet (for the delegates)

`research/residcorr_out/diagnostics.jsonl` — 1,827 daily rows, 1,642 with
a defined matrix. Headline aggregates (§63.3): daily median off-diagonal
residual correlation has median 0.255 across dates (p5–p95: 0.18–0.38);
λ₁ share median 0.307. Stage 21a (§63.5) corrected the benchmark to the
finite-sample `m = 87` independence null (`null_ratios.jsonl`): the
leading-mode share is a median **7.6× the Marchenko–Pastur edge** (p5
3.1×), the Frobenius distance a median **2.9× its RMS null scale** — the
spherical independence null is strongly contradicted at every percentile.
Caveat on record: the null is spherical/i.i.d. conditional on the design;
`m_eff` under temporal dependence is not estimated. D.4 prohibition
stands: no strategy component may run under the measured covariance
before the fixture is frozen.

## Module map

```
rcm/seal.py         the structural seal (+ unlock via committed ledger entry)
rcm/timeline.py     one holding interval for everything (§60.4/§60.11.2)
rcm/factors.py      ETH⊥, OLS betas, V_i, factor covariance (one 90d window)
rcm/momentum.py     score, PIT calibration set-builder, shrinkage, carry guard
rcm/funding.py      cadence-aware F̂ via Gen-1 machinery; equality observability
rcm/optimizer.py    SOCP (γ and dollar-tolerance eliminated; η = the cost)
rcm/rescov.py       Ω̂: MP-edge residual factor covariance (§63.6.4) + full model
rcm/gates.py        N_eff/leg, bounded C_signal (S=|μ_mom|), V_ret ≥ 0.40
rcm/statemachine.py causal calendar classifier + hold/rescale/flatten(M=7)
rcm/attribution.py  Δ_gate, Δ_transition, reporting tuple, the literal label
```

Gen-1 (XSMOM) remains frozen and complete at 15 of 25 trials. Holdout sealed
under two independent seals — one of them now executable.
