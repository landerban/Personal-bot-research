# Stage 19b (v2) — Architecture amendment after F-1

Stage 20 found a real design incompatibility (F-1): the optimizer concentrates
in proportion to alpha dispersion; the `N_eff ≥ 6` identity gate then rejects
the day. This stage (i) proves the diagnosis with a **non-vacuous** witness,
(ii) adopts the *principle* that breadth is part of construction and admits
only **mathematically exact** forms, (iii) fixes two "mechanical" resolutions
that were not, and (iv) sends the stress fixture back. **No coefficient, form,
or threshold is chosen by comparing performance or synthetic metrics; none is
invented to turn the xfail green.**

Append as `NOTES` §62. **No real market or return data. Gen-2 stays 0 of 20.**
Holdout sealed. §0 of `STAGE2_PROMPT.md`, §59, §60, §60.11 govern.

---

## 0. Ledger wording correction first

§61.2's "realistic alpha shapes" and "predicts chronic `D_gate` days under
real momentum" overreach: no Gen-2 real return has been examined. Append:
the fixtures are **non-flat synthetic alpha profiles**; the established finding
is **"the current optimizer does not structurally guarantee the frozen breadth
invariant."** It does *not* establish that real RCM will chronically fail
formation. The first is sufficient to block progression.

## Part A — The F-1 witness, non-vacuous

§61.2 recorded concentrated optimizer outputs but no feasible broad witness.
Add it on the identical instances (linear and steep profiles, same seeds).

**A.1 Non-vacuity.** Because the volatility constraint is an upper bound and
`g_min` is unresolved, a broad book can be made trivially feasible by scaling
toward zero (`εw` shrinks risk, factor exposure, and cap usage while leaving
`N_eff` unchanged). The witness must therefore be **scale-comparable to the
rejected target**:

```
G(w_broad) = G(w_rejected)       within numerical tolerance
```

Gross equality is coefficient-free and auditable.

**A.2 Constraints.** At that gross, `w_broad` satisfies: `1ᵀw = 0`;
`wᵀΣw ≤ σ²_target`; the chance constraints; `‖w‖₁ ≤ 3`; `|w_i| ≤ 0.25`;
`N_eff,long ≥ 6`; `N_eff,short ≥ 6`. Construct it explicitly; record weights
and per-constraint slack.

| Witness | Diagnosis |
|---|---|
| exists at the rejected gross; optimizer still returns the concentrated reject | **construction/gate incompatibility proven** → Part B |
| does not exist at that gross | **constraints/fixture incompatibility** — stop, report; Part B does not run |

## Part B — Breadth is part of construction (conditional on Part A)

**B.1 Principle, adopted.** If per-leg effective breadth defines what an RCM
portfolio is, construction must be breadth-aware. Rejected: **(b)** re-deriving
the floor because a fixture gave 5.4 (fitting to a failed test); **(c)**
tolerating chronic skips (recreates the Gen-1 failure; contradicts the frozen
0.60 formation-rate criterion).

**B.2 Admissibility rule.** A form is admissible only if it (i) introduces no
new free coefficient (or one derived from a prior invariant) **and** (ii)
enforces breadth on the **net** portfolio **mathematically** — not empirically,
not via cost discouragement, not via the post-hoc gate.

**B.2.1 REJECTED unless made exact: the split-variable SOC form.** With
`w = w⁺ − w⁻`, per-leg `N_eff ≥ 6` is the SOC `‖w⁺‖₂ ≤ 1ᵀw⁺/√6` (and for
`w⁻`), coefficient-free — but the solver can hold `w⁺_i = 0.10, w⁻_i = 0.09`
for one name, satisfying both leg constraints with a net 0.01 position. **This
is fundamental, not a weakness to examine:** exact complementarity
`w⁺_i·w⁻_i = 0` is non-convex and cannot be added to the SOCP without changing
problem class. Admissible only if an exact non-padding construction is
established; if that requires mixed-integer or non-convex optimization, record
it explicitly and evaluate it as a **solver-architecture change**. Empirical
absence of padding is **not sufficient**.

**B.2.2 Candidate: leg membership pre-assigned by signal sign.** Assign each
eligible name to L or S by `sign(μ_i)` **before** the optimizer; constrain
`w_i ≥ 0` on L, `w_i ≤ 0` on S. No split variables exist, so padding is
impossible by construction, and the per-leg SOC breadth constraints act on
genuinely disjoint sign-restricted subvectors using the frozen 6 — convex,
exact, coefficient-free. **Stated cost:** the optimizer loses the freedom to
hold a name against its signal for hedging purposes, so the chance constraint
may bind harder and non-formed days may rise. Semantically this is close to
what the hypothesis means (hold a name only on the side its residual momentum
indicates); whether the hedging cost is acceptable is a synthetic structural
question, tested per B.4. Record it as a candidate with this cost, not as
adopted.

**B.2.3 Concentration penalty.** Rejected unless its coefficient derives from
a prior invariant. "The coefficient that makes the xfail pass" is not a
derivation.

**B.2.4 Other forms** the agent proposes: same admissibility rule.

**B.3 Candidate-selection safeguard.** If more than one form satisfies all
invariants, **do not select among them by synthetic objective value, achieved
alpha, turnover, concentration margin, or distance from the F-1 boundary** —
synthetic testing must not become its own optimization dataset. Prefer the
form with the **smallest architectural departure** from the frozen
specification; if that does not uniquely determine one, **leave the choice
UNRESOLVED**.

**B.4 Verification, synthetic only.** Any adopted form passes the F-1 fixture
on all seeds **and** a padding-abuse fixture (for split forms) or a
hedge-infeasibility fixture (for sign-pre-assigned forms, reporting the
formation consequence), with `Σ`, `ε_β`, `σ_target`, the 0.25 cap and all §60
frozen quantities unchanged. If no admissible form survives, record it and
stop — the incompatibility stands as a finding.

## Part C — Stale-book risk invariant

§61.3.1's "clamp to `G_cap = 3`" permits a book formed at 0.50 gross to run at
3.0 — Gen-1's leverage-drift failure by another route. Adopt:

```
G_ref = gross of the last successfully formed executable portfolio
```

**C.1 The invariant, coefficient-free:** while carrying a stale book,
`G_t ≤ G_ref`. On a non-formed day apply the single scalar

```
α_t = min(1, G_ref / G_t)
```

to all positions. **Downscale only:** stale exposure that has grown beyond its
last valid scale is reduced to it; stale exposure that has shrunk is **never
levered back up** — increasing exposure without a current valid decision is
adding risk without a strategy. No deadband, no threshold. `G_cap` remains the
catastrophic backstop only.

**C.2 Lifecycle.** `G_ref` is undefined before any formation (no position ⇒
no rescale); set at each successful formation; **cleared by the M=7 forced
flatten** and re-set at the next formation; persisted as bot-owned state
(§54.2 class) across restarts.

## Part D — `degenerate_target` ⇒ `D_degenerate`, a fifth category

A valid `w = 0` is an economic decision — expected returns net of costs do not
justify exposure — not a data failure. §61.3.2's `D_structural` misclassifies
it. `D_formed` is also wrong: repeated zero books would count as successful
formations, letting the frozen 0.60 formation-rate criterion read 90% while
exposure is held on 20% of days.

Adopt:

```
D = D_formed ∪ D_gate ∪ D_structural ∪ D_operational ∪ D_degenerate    (disjoint)
```

`D_degenerate`: counts in the calendar denominator; **not** formed; **not** a
feasibility-gate failure; `r_shadow = 0` exactly; **excluded from Δ_gate** (it
is not a rejected feasible target); reported separately in the tuple (extend
§59.11.4 by appending the field). Place it in the causal-precedence order at
the optimizer stage. Still exactly one category per date; still no NaN.

## Part E — The correlated-residual stress fixture: back for derivation

§61.5's `ρ ∈ {0.3, 0.6}`, `R_σ ≤ 1.5`, breach ≤ 27% is **not approved**: the
correlations reference no invariant; 1.5 derives from Gen-1's Stage-43 20%
drawdown *selection* cap, which §59.6 did not inherit as an RCM model-error
tolerance; and drawdown headroom does not map linearly to volatility-model
error under path dependence.

Re-derive from RCM-owned invariants (`σ_target`, `G_cap`, `ε_β`, the 10%
nominal breach, the inherited 30% kill switch **only** with an explicit,
defended vol→drawdown argument) — **or leave UNRESOLVED**. Ending Stage 19b
with "stress test UNRESOLVED" is acceptable; inventing `ρ = 0.5` is not.

## Part F — Pending user decisions, listed not resolved

`g_min` (§60.11.6) and zero-momentum semantics (§60.11.8, both delegates
recommend (a)). Still blocking trial 1.

## Order of work

1. §0 correction appended
2. Part A: same-gross witness; diagnosis recorded
3. Part B (if incompatibility proven): apply B.2 admissibility to each
   candidate; B.3 safeguard; B.4 verification; adopt one, or UNRESOLVED, or
   "no admissible form"
4. Parts C, D adopted as specified
5. Part E re-derived or UNRESOLVED
6. Re-run all suites; the F-1 xfail flips **only** via an adopted Part B form,
   and the flip is explained in §62
7. Report. **Gen-2 0 of 20.**

## Acceptance

- §62 appended; §61 unedited; wording correction recorded
- Witness at the rejected target's gross, with per-constraint slack — or its
  non-existence at that gross proven
- B.1 adopted; (b)/(c) rejected; each candidate judged by the B.2 rule;
  split-variable form rejected unless exactness established; B.3 safeguard
  applied; B.4 fixtures present
- `G_ref` with downscale-only invariant `G_t ≤ G_ref`, lifecycle incl. M=7
  clearing and persistence
- `D_degenerate` adopted with its five properties; tuple extended by appending
- Stress fixture re-derived from RCM-owned invariants or UNRESOLVED
- `g_min`, zero-momentum listed pending
- **No real data; Gen-2 0 of 20; holdout sealed**

## Do not

- Accept a witness that wins by scaling toward zero
- Adopt any breadth form whose net-portfolio guarantee is empirical rather
  than mathematical
- Select among surviving forms by any synthetic metric
- Lever a stale book back up toward `G_ref`, or use `G_cap` as the rescale
  reference
- Classify a valid zero target as formed or structural
- Approve the 0.3/0.6/1.5 chain
- Change 6, `σ_target`, `ε_β`, the 0.25 cap, or any §60 frozen quantity
- Resolve `g_min` or zero-momentum in code; touch real data or the holdout
