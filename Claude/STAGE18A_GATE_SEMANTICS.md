# Stage 18a (final) — §59 amendment: gate-failure semantics and feasibility attribution

**No code. No market data, return data, snapshots, backtest results, or
performance diagnostics.** Repository text and the ledger may be read.

Appended to `NOTES` §59 as **§59.11** (append-only — §59.0–§59.10 unedited).
Section 1 is **blocking for Stage 19**.

§0 of `STAGE2_PROMPT.md` remains in force. Gen-1 frozen, 15 of 25. Holdout
sealed. Gen-2 budget 0 of 20.

---

## 1. BLOCKING — Gate-failure state semantics are part of the specification

§59.4 defines a gate failure as a **skip** but never says what a skip does to an
**already-held portfolio**. The options are materially different strategies:
flatten to cash | hold unchanged | risk-only rescale | partial rebalance |
hold N days then flatten.

Record verbatim:

> **Gate-failure state semantics are part of the strategy specification.**
> "Skip" states only that no new gate-passing target exists; it does not by
> itself specify what happens to an already-held portfolio. **Stage 19 must
> pre-register the exact state transition** — flatten, hold, risk-only
> rescale, or another explicitly defined rule — **before any return data are
> accessed. The rule may not be selected from historical performance.**

**1.1 Gen-1's skip semantics are NOT inherited.** §59.6 lists inherited
infrastructure and is silent here; silence must not become an unexamined
default. Gen-1's skip was all-or-nothing on a discrete 5L/5S book; RCM's
continuous optimizer can produce a **partially feasible** book — a state Gen-1
never had. The rule is derived from RCM's own architecture.

**1.2 A single common transition is PREFERRED.** Gate-specific transitions are
permitted **only** where Stage 19 establishes that a common transition violates
a distinct risk or economic invariant, with that invariant named. Bespoke
behaviour per gate is a state machine's worth of degrees of freedom before RCM
has demonstrated anything.

**1.3 Concurrent gate failures must be deterministic.** Stage 19 must specify
either one common transition for any non-empty failure set, or an explicit
precedence/composition rule `T(failed_gates, current_state)`. **Implementation
order may not determine the economic outcome** — `if/elif` ordering is not
strategy logic.

**1.4 Leverage-drift consequence stated.** Any "hold" variant must state its
leverage-drift consequence explicitly. Gen-1's runaway leverage came from
exactly this choice being made without stating it.

## 2. Exhaustive calendar classification, with precedence

Every date is classified into **mutually exclusive, collectively exhaustive**
categories by a deterministic rule:

```
D = D_formed ∪ D_gate ∪ D_structural ∪ D_operational      (disjoint)
```

- `D_gate` — failed one or more §59.4 feasibility gates
- `D_structural` — insufficient usable universe, missing market data,
  unavailable execution bar, factor-estimation failure, stale metadata
- `D_operational` — solver failure, refusal, harness/host issues

No date may be unaccounted for.

**2.1 Calendar-class precedence.** A date can satisfy more than one raw failure
condition (gates fail *and* the execution bar is missing). Since the categories
are disjoint, **Stage 19 must define deterministic precedence** so
classification is invariant to control-flow ordering. Precedence must reflect
**causal stage ordering** — the first stage at which the intended decision
became impossible — not `if/elif` placement. A plausible pipeline is
`structural eligibility → optimizer → feasibility gates → execution`, but
Stage 19 defines it.

## 3. Attribution diagnostics — two deltas, both fenced

**3.1 The object.** The shadow book **is the canonical pre-feasibility book
already defined in §59.4** as the signal-coverage denominator. **One object,
two uses**; a second definition may not be introduced.

**3.2 The shadow return, frozen.** A pure signal-attribution quantity:

```
r_shadow(t+1) = w_pre(t)ᵀ · r_price(t+1)
```

canonical **continuous** pre-feasibility weights, next-rebalance-horizon
**price** returns, **no fees, no slippage, no quantization**. Funding reported
separately, never folded in.

**3.3 Domain.** Computed over `D_formed ∪ D_gate` only — no shadow target
exists on `D_structural` or `D_operational` dates. Those counts are reported
and explicitly excluded.

**3.4 Δ_gate — were rejected targets systematically different?**

```
Δ_gate = E[r_shadow | formed] − E[r_shadow | gate_failed]     with 90% CI
```

Stationary-bootstrap interval. **Not significance-tested as pass/fail.**

**3.5 Δ_transition — what did the transition rule do about it?**

```
Δ_transition = E[ r_actual_price − r_shadow | D_gate ]        with 90% CI
```

`r_actual_price` is the realised book's **price-only** return under the
pre-registered transition rule — price-only so it is comparable to `r_shadow`.
This separates *alpha selection caused by gate timing* (Δ_gate) from
*performance effect caused by the chosen transition* (Δ_transition), giving the
decomposition:

```
signal → feasibility selection → state transition → realised strategy
```

**3.6 The decomposition is price-only and does NOT sum to realised net
performance.** The gap is execution cost, and it is **not neutral across
transition rules**: flatten *trades* on gate-failed days and incurs closing
costs; hold trades nothing. Report the execution-cost term as a separate line
and name the transition rule's own cost consequence. Otherwise a positive
Δ_transition under flatten reads as free protection when part of it was paid
for in fees.

**3.7 Interpretation — transition-induced exposure selection.**

> **Literal protective abstention exists only under flatten semantics.** Under
> hold, rescale, or other transitions, gate failures can still create
> protective or harmful **exposure selection** by preventing the canonical new
> target from replacing the existing book. Therefore **Δ_gate measures
> properties of rejected targets**, while the realised performance consequence
> of gating must be interpreted **jointly with the pre-registered transition
> rule** (Δ_transition). Every report of either diagnostic states the active
> transition rule beside it.

Further, non-categorically:

> Systematically worse shadow alpha on gate-failed days is **evidence
> consistent with endogenous time selection induced by feasibility**. It is a
> potential contributor to performance and **may not be attributed to residual
> momentum without further decomposition** — volatility, liquidity and listing
> age can produce the same pattern.

**3.8 The fence.**

> Δ_gate and Δ_transition are **attribution only**. Neither may be used to tune
> feasibility thresholds, nor to choose flatten vs hold vs rescale after seeing
> returns, nor to convert a feasibility rule into an alpha filter. Any of those
> creates a **new strategy generation** with its own governance, not an
> amendment to RCM v1.

## 4. The standard reporting tuple

Every performance table row reports:

| Field | Required |
|---|---|
| Calendar performance (full calendar, §59.4.1) | yes |
| Formation rate | yes |
| Feasibility-gate skip rate | yes |
| Structural skip rate | yes |
| Operational skip rate | yes |
| Gate composition (which gates, counts) | yes |

Any Sharpe, return, or drawdown computed on formed days only must carry the
literal label:

**`DIAGNOSTIC — CONDITIONAL ON FORMATION — NOT STRATEGY PERFORMANCE`**

---

## Order of work

1. Append `NOTES` §59.11 containing §1–§4, dated, §1 marked **blocking for
   Stage 19**
2. Confirm §59.0–§59.10 byte-identical
3. Report

## Acceptance

- §59.11 appended; earlier subsections unedited
- §1 verbatim incl. non-inheritance, common-transition preference, concurrent
  determinism, leverage-drift; marked blocking
- §2's disjoint exhaustive categories **and** the §2.1 precedence requirement
- §3: same-object rule, frozen shadow return, domain restriction, both deltas
  with CIs and no pass/fail, the price-only/execution-cost caveat, the
  transition-induced-exposure-selection wording, the fence
- §4 tuple and literal label
- **No code. No market/return/performance data accessed. Gen-1 15 of 25;
  Gen-2 0 of 20. Holdout sealed.**

## Do not

- Edit or restructure any existing ledger section
- Choose the transition rule, precedence rules, or any threshold here —
  Stage 19 derives them
- Introduce a second definition of the pre-feasibility book
- Fold fees, slippage, quantization, or funding into `r_shadow` or
  `r_actual_price`
- Significance-test either delta as a gate, or use either to choose a
  transition rule or tune thresholds
- Access market, return, or performance data
