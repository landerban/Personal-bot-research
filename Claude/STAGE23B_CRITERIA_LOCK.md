# Stage 23b — §60.12 (criteria completed, user decisions recorded), the two evaluators, then §64 — and STOP

Three parts in strict order. **Part I** appends `NOTES` §60.12: the completion
of §60.8's two development criteria, including the three **user decisions
made before trial 1 and before any return read**. **Part II** implements the
two life-or-death evaluators and tests them synthetically. **Part III** writes
the corrected trial-1 pre-registration as `NOTES` §64 and **stops** for both
delegates' final approval. **No return is read. No run. Gen-2 stays 0 of 20.**

§0 of `STAGE2_PROMPT.md`, §59, §60 as amended (§60.11, §62, §62.8, §63)
govern. Holdout sealed; Gen-2 runner hard-rejects 2025-01→2026-07.

Also do Stage 23 Part 0 first: the §63.7 input-`C` eigenvalue policy
(`λ_min(C) < −SOLVER_TOL` ⇒ integrity failure; roundoff band proceeds; **no**
PSD projection or nearest-correlation correction), with its second-branch test.

---

# PART I — §60.12: completing the two development criteria

## I.1 Scope statement

Trial 1 evaluates the **two** development-applicable kill criteria of §60.8:
the 63-day formation criterion and the residual-momentum IC criterion. The
21-day forward feasibility gate is **reserved for forward validation** and
recorded as not evaluated, so its absence can never be read as a pass.

## I.2 Criterion 1 — formation, completed

**I.2.1 Structural capability (derived).** `N_eff,leg ≥ 6` on both legs is
impossible with fewer than **12** names in the pre-alpha PIT risk-eligible set
(`N_eff ≤ #names`). 12 is derived from the frozen 6.

**I.2.2 `evaluation_start` — USER DECISION, recorded verbatim:**

> `evaluation_start` = the first UTC calendar date on which the frozen RCM
> pipeline is structurally capable of a complete decision **and** the pre-alpha
> PIT risk-eligible set contains at least 12 names.

Recorded as an interpretation choice approved by the owner before any return
was read — not algebra.

**I.2.3 Calendar time is never compressed.** From `evaluation_start` onward,
**every UTC calendar date counts** in the window denominator — `D_structural`
(including a later fall below 12 names), `D_operational`, `D_degenerate`, and
gate-failed dates all count as non-formed. Only `D_formed` enters the
numerator. Rationale recorded: the 0.60 threshold was derived against real
consecutive-day failure runs and the `M = 7` stale-book ceiling (§60.6/§60.8);
compressing dead days out of the index would hide exactly what it detects.

**I.2.4 The rule, exact:**

```
FR_t = #{ D_formed in [t−62, t] } / 63     for every completed 63-calendar-day
                                            window with t ≥ evaluation_start + 62
0.60 × 63 = 37.8   ⇒   ≤ 37 formed days = window FAIL;  ≥ 38 = window PASS
ANY completed window failing ⇒ criterion 1 FAIL
```

"Trailing" read as **every completed window** — the stricter reading of an
ambiguous frozen sentence; the conservative direction. The forward gate
inherits the same semantics over 21 calendar days: `0.60 × 21 = 12.6` ⇒
`≤ 12` fails, `≥ 13` passes, once 21 forward calendar dates exist.

## I.3 Criterion 2 — the IC statistic, completed

**I.3.1 Estimand — USER DECISION, recorded verbatim:**

> Equal-weighted daily cross-sectional Spearman IC:
> `IC_t = Spearman_i( Z_mom,i,t , ε_fwd,i,t )`, and `IC̄ = (1/T) Σ_t IC_t`,
> every eligible date weighted equally irrespective of cross-section size.

Recorded as a **user-approved statistical-design completion** of §60.8's
ambiguous "pooled cross-sectional Spearman" — explicitly **not** claimed to
follow from the word "pooled." The alternative estimand (a single Spearman
over all asset-date pairs) is a different, coherent estimand that weights
large cross-sections more heavily; it was considered and not chosen. *(The
earlier claim that pooled Spearman "would not admit a coherent stationary
bootstrap" was false and is withdrawn — date blocks can be resampled carrying
their cross-sections.)*

**I.3.2 Evaluation cross-section.** The frozen pre-alpha PIT risk-eligible
universe at `t` with a complete `ε_fwd,i,t`. **Not conditioned** on portfolio
capability, formation, weights, sign partition, gates, or PnL. Criterion 2
tests the **signal**, not portfolio feasibility: a 10-name date cannot form a
book but its ten observations still bear on whether residual momentum
predicts next-day residuals. Criterion 1's 12-name capability rule does **not**
apply here.

**I.3.3 `ε_fwd`.** The §60.11.2.2 execution-horizon forward residual, betas
fixed at the signal date. Ex-post use of `t+1` outcomes is proper for a
kill-criterion evaluation and does not conflict with the §60.11.2 PIT
set-builder, which governs `b_t` calibration inside the strategy.

**I.3.4 Ties:** average ranks.

**I.3.5 Undefined dates.** If `IC_t` is mathematically undefined — fewer than
two valid paired observations, or a constant rank vector — the date is
**excluded** from the mean and the bootstrap, and **counted and reported with
its reason**. It never becomes zero silently.

**I.3.6 Stationary bootstrap** on the chronologically ordered defined `IC_t`
series:
- **2,000 replicates** — Gen-1 precedent (confirmed)
- **mean block length `n^{1/3}`**, `n` = number of defined `IC_t` dates —
  Gen-1 precedent (confirmed), recomputed for this series
- **Interval construction: inherit Gen-1's bootstrap *code*, not its prose.**
  The ledger says "90% CI" without naming percentile/basic/BCa; the Gen-1
  implementation is the frozen precedent. Locate it, cite file and hash in
  §60.12, and reuse it. If it cannot be located, **stop and report** — do not
  choose a construction.
- **Two-sided 90%** — inherited convention (§60.12 records: `CI_lower > 0` is
  equivalent to a one-sided 5% test)
- **Seed derived deterministically from the §64 lock-commit hash** — recorded
  as **new engineering governance for reproducibility**, not precedent

**I.3.7 Criterion, binary:** `CI_lower(IC̄) > 0 ⇒ PASS`; otherwise **FAIL**.
No INDETERMINATE branch: §59.3.1's rule governs comparisons between
specifications; this is a kill test against the null `IC = 0`, and §60.8 made
it binary.

**I.3.8 MDE disclosure — frozen in place of a fabricated number:**

> No exact numerical MDE is identifiable before Trial 1 under the frozen
> stationary-bootstrap IC procedure without observing the return-derived
> dependence structure of the daily IC series. No calendar-count proxy is
> substituted. Criterion 2 tests existence/sign solely through the frozen
> two-sided 90% bootstrap CI. The realized CI half-width is reported
> afterward as observed resolving precision and is not a second criterion.

Recorded explicitly as the §59.3.1 operationalization for this criterion.

## I.4 VOID accounting — USER DECISION: inherit Gen-1

```
attempt_id         += 1 for every real-data execution
valid_trial_count  += 1 only if void == false
```

A demonstrably defective execution (defect reproduced by a failing test
against the pre-fix code that passes after the fix) is marked `void: true`,
remains permanently in `trials.jsonl`, and **does not consume valid budget**
— Gen-1 precedent (all 13 Gen-1 void rows retained, budget unconsumed). A
corrected run is the next attempt and remains valid Trial 1 if none has
completed. "Struck from the record" = struck **as evidence**, never deleted.

## I.5 The reading table — fixed, four rows, no discretion

| Result | Consequence |
|---|---|
| Formation PASS + IC PASS | §64 commit = RCM v1 freeze (§59.1.6 hash + UTC); forward paper begins; valid trials 1/20 |
| Either criterion FAIL, no demonstrable implementation defect | RCM v1 **abandoned** (§59.7); valid trials 1/20 |
| Apparent FAIL with reproduced implementation defect | attempt VOID; valid trials 0/20; fix pinned by test; next attempt under identical locked criteria |
| Operational run error not shown to be a measurement-invalidating defect | non-VOID attempt; valid trial consumed |

**No post-result user discretion. No path from a PnL number to keeping a
signal whose IC criterion failed.**

# PART II — The two evaluators, built and synthetically tested

`rcm/eval_formation.py` and `rcm/eval_ic.py`, implementing I.2/I.3 verbatim,
importing no return reader (import-level test). Tests, all synthetic:

| Evaluator | Fixture |
|---|---|
| Formation — boundary | planted calendar: a window with exactly 37 formed ⇒ FAIL; 38 ⇒ PASS |
| Formation — `evaluation_start` | planted eligibility counts crossing 12 on a known date with histories present ⇒ start detected on that date, not before |
| Formation — no compression | a run of dead calendar days between capable dates counts in the denominator; an index-compressed implementation would pass, this one must fail the planted window |
| Formation — every window | one failing window among many passing ⇒ criterion FAIL |
| Formation — categories | `D_degenerate`, `D_structural`, `D_operational`, gate-fail dates all counted non-formed |
| IC — sign | planted positive daily IC ⇒ `CI_lower > 0`; planted zero ⇒ CI straddles zero; planted negative ⇒ FAIL |
| IC — equal weighting | a 200-name date and a 12-name date each contribute weight 1 to `IC̄` (an asset-date-pooled implementation would not) |
| IC — no capability gate | a 10-name date with a defined Spearman is **included** |
| IC — undefined dates | constant-rank and <2-pair dates excluded, counted, reasoned; never zero |
| IC — bootstrap | 2,000 replicates; block length `n^{1/3}`; interval construction identical to the located Gen-1 code (a test asserts the same function/hash); deterministic under the seed rule |
| Both | `ε_fwd` and the evaluation universe are the frozen objects (single-definition tests) |

# PART III — §64: the pre-registration, corrected — then STOP

Write §64 per Stage 23's structure with these corrections applied:

- §64.1: the **two** criteria, the one question, criterion 3 reserved
- §64.2: transcribe I.2–I.3 quantities verbatim; the I.3.8 MDE disclosure;
  the I.5 table; bug-vs-design by reproducing test; I.4 accounting
- §64.3: Stage 23 Part C reporting unchanged (tuple, literal label,
  full-calendar headline, `Δ_gate`/`Δ_transition`, carry series, `K_t`
  series; no Gen-1 comparison as headline)
- §64.4: the **lock commit pins the evaluator code and hashes** as well as
  the specification; trial logged `pre-registered` with `attempt_id = 1`,
  `valid_trial_count = 0`; run-stage immutability test present
- Delete from the Stage 23 draft: the MDE-as-gate rule, the INDETERMINATE
  branch, the post-result user-decision branch

**Then STOP.** Both delegates review §64 as written. The run is a separate
stage.

## Acceptance

- §63.7 policy appended and tested
- §60.12 records I.1–I.5 verbatim in substance, with the three user decisions
  labelled as such, the withdrawn false claim noted, precedent vs new
  governance labelled correctly, and the Gen-1 CI code located and cited (or
  the stage stopped)
- Both evaluators implemented; every Part II fixture green
- §64 written with corrections; trial `pre-registered`, `attempt_id = 1`,
  `valid_trial_count = 0`
- **No return read. No run. Gen-2 0 of 20. Holdout sealed.**

## Do not

- Compress calendar time in criterion 1, or gate criterion 2 on capability
- Fabricate an MDE number, or reintroduce an INDETERMINATE or user-decision
  branch
- Choose a CI construction — inherit Gen-1's code or stop
- Let a void consume valid budget, or delete a void row
- Move trial 1 to `started`, or read a return
