# Stage 21 (v2) — Three decisions recorded; two implemented; one measured for the delegates

The user has supplied the risk preferences that blocked trial 1. This stage
records them verbatim, implements the two decidable now, and — under an
**explicit, recorded reordering of the research plan** — runs the structure
measurement the delegates need to decide the third, with the **measurement
protocol frozen before a single return is read.** No performance quantity is
computed anywhere. Gen-2 stays **0 of 20**.

Append as `NOTES` §63. §0 of `STAGE2_PROMPT.md`, §59, §60, §60.11, §62, §62.8
govern. Holdout sealed; the Gen-2 runner's hard rejection of 2025-01→2026-07
stays in force.

Prerequisite: §62.8 with F-1 RESOLVED post-verification.

---

## Part A — The decisions (§63.1), dated, before code

**A.1 Zero-momentum semantics — USER DECISION: (a), trade.** When
`Σ|w_pre||μ_mom| = 0`, signal coverage is **N/A** (a distinct value; not a gate
failure); the book may form under the §62.8 centered construction and carries
the literal label `CARRY REGIME — NOT RCM` per §60.2.3. The rule on record,
confirmed. §60.11.8 resolved.

**A.2 Exposure retention — USER DECISION: 40% of intended variance.** The
withdrawn gross gate (§60.11.6) is replaced by:

```
V_ret = (w_realᵀ Σ w_real) / (w_preᵀ Σ w_pre)  ≥  0.40
```

Recorded as **the risk owner's preference, stated before any Gen-2 return
exists** — same class as the 10% vol target and 30% kill switch; not derived
from and not adjustable by performance. Units explicit: 40% of *variance*
≈ 63.25% of intended *volatility* under proportional scaling; the user has
been informed. `G_realized / G_pre` stays in the tuple as **diagnostic only**.

**A.2.1 `Σ` is the optimizer's covariance model, whatever it is at freeze.**
Not "the diagonal model as of today": Part D exists precisely because the
diagonal approximation may be replaced before RCM v1 freezes. `w_pre` and
`V_ret` must always use **the same** estimator — one risk model, two uses. The
**0.40 is permanently fixed**; the model it is evaluated under is whatever the
frozen specification adopts.

**A.2.2 Companion invariant — the absolute ceiling still binds.** `V_ret` is a
lower bound only and does not prevent `V_ret > 1`. The executable book must
still satisfy the frozen `w_realᵀΣw_real ≤ σ²_target,daily`. `w_pre` satisfies
it by construction; quantization and composition changes happen downstream and
could regain risk. Enforcing an existing invariant, not a new tolerance.

**A.3 Residual-correlation robustness — DELEGATED** to the two reviewers,
fixture and criteria both. Process: (i) Part D measures development-era
residual-correlation **structure**; (ii) the delegates jointly record fixture
+ criteria + derivation in a §63 append **before** any stress test executes;
(iii) neither changes afterward. §62.5: UNRESOLVED → DELEGATED-PENDING-
MEASUREMENT.

**A.3.1 Naming, fixed now.** The resulting requirement is
**development-informed robustness calibration** — legitimate because 2020–2024
is the development set — and **may never later be cited as independent
evidence that the covariance model generalizes.**

**A.4 Reordering amendment — recorded explicitly.** §59.9 fixed
`synthetic/structural → 2020–2024 development`, and §60.11.5 placed the
correlated-residual test in the pre-real-data structural work. Stage 21
departs from that:

> §63 authorizes **one narrowly scoped development-era structure
> measurement** before completion of the correlated-residual synthetic stress
> test. This is a **deliberate amendment** to the prior synthetic→development
> ordering, justified because no defensible correlation fixture can be derived
> from existing architecture alone (§62.5). It accesses no alpha or
> performance result and **authorizes no other development-data use** before
> the stress requirement is frozen.

## Part B — Implement A.1

- Remove the UNRESOLVED raise; the §62.8 §3.3 fixture asserts actual formation
  with the label.
- Coverage returns a distinct `N/A` value (not 0, 1, or NaN); tuple records it.
- Such a day is `D_formed` with the label — **not** `D_degenerate`.
- **Label test, corrected:** exact-zero-momentum formed days **must** carry the
  label; **other carry-dominant days also carry it** when the frozen §60.2.3
  rule fires (trailing 21-day `s_mom < 0.5`). The test asserts presence on
  both classes and absence only when neither condition holds.

## Part C — Implement A.2

- `V_ret ≥ 0.40` in the gate layer, evaluated under the optimizer's `Σ`
  (A.2.1). A single test asserts the gate and the optimizer reference the same
  covariance object.
- **Absolute ceiling test (A.2.2):** construct a quantization/composition case
  where `w_real` would exceed `σ²_target`; assert the executable book is
  rejected or rescaled and cannot exceed the frozen 10% target.
- Tests: `w_real = g·w_pre ⇒ V_ret = g²` exactly; composition-changing drops
  ⇒ `V_ret ≠ g²`; fires at 0.3999, passes at 0.4001.
- **Zero-denominator semantics, split:** `w_pre = 0 ⇒ D_degenerate`;
  **`w_pre ≠ 0` with `w_preᵀΣw_pre = 0` (or non-finite) ⇒ covariance/model
  integrity failure — fail closed**, classified `D_structural`, alert raised.
  A nonzero book in a modeled zero-variance nullspace is a singularity, not an
  economic zero. Near-zero numerical cases reuse the **already-frozen §60.7
  numerical tolerance** — no new threshold.
- Manifest delta: `g_min — WITHDRAWN §60.11.6 → V_ret ≥ 0.40, user preference
  §63.1.A.2`. The 0.40 appears once in config, cited; grep test.

## Part D — The residual-correlation measurement: protocol FROZEN before reading

**D.0 Freeze this protocol in §63 before the module reads any return.** Every
object below is defined now; nothing is decided after seeing output.

**D.1 Scope and nature.** Development window **2020-01-01 → 2024-12-31**.
Frozen §60.1 factor model (ETH⊥, frozen beta window). **Estimation under
§60.0**; not a trial. No Sharpe, PnL, formation rate, attribution, or
comparison between specifications. `diagnostics.jsonl`. An import-level test
asserts the module cannot reach portfolio, gate, optimizer, or PnL code.

**D.2 Protocol, exact.**
- **Window:** the frozen **90-day** covariance estimation window — the single
  window the architecture already owns. No other window.
- **Dates:** one correlation matrix per eligible daily decision date `t`,
  from residuals over `(t−90d, t]`.
- **Set:** the PIT structurally eligible universe at `t` with **complete,
  aligned** residual observations over the window. **No pairwise deletion** —
  pairwise-complete matrices can be non-PSD and make eigen-diagnostics
  internally inconsistent. Names lacking complete observations are excluded
  from that date's matrix; the count is reported.
- **Statistics per date** (all reported as raw daily time series; no narrative
  labels):
  1. `N_t` — matrix dimension
  2. off-diagonal pairwise correlations: median, 25th/75th, **5th/95th**
     percentiles (these are "the tails"; no other definition)
  3. eigenvalue shares `λ_k / Σλ` for `k = 1, 2, 3, 5`, and the diagonal-model
     expectation `1/N_t` beside each
  4. **one-factor residual:** the spectrum after removing PC1 —
     `λ_2..λ_N / Σ_{k≥2} λ_k` for `k = 2, 3, 5` — reported, not adopted
  5. Frobenius distance between the correlation matrix and identity, per date
- **Time variation:** the raw series above, plotted. **No "regime" statistic**
  and no clustering label — the delegates read the series; they do not receive
  a story.
- **Aggregates:** over all dates, the distribution (same percentiles) of each
  per-date statistic.

**D.3 What this is and is not — corrected wording.**
D.2 is **not** a basis for choosing alpha, signal, portfolio, or performance
parameters. It **is explicitly the development-informed calibration input for
the residual-correlation robustness fixture and acceptance criterion**, per
A.3. Those two statements are both true and are recorded together.

**D.4 Delegate prohibition, recorded.** Before fixture and criteria are frozen,
**no one may run the optimizer, the gates, or any strategy component under the
measured correlated covariance** to observe whether the diagonal model
"passes." D.2 exposes residual structure only — never strategy survival under
that structure. Violating this would let the criterion be chosen around the
answer.

**D.5 Boundaries.** A test requests one day into 2025 and is refused. The
module reads nothing else.

**D.6 Then STOP.** The stress test is not run. The delegates receive D.2,
record fixture + criteria + derivation in a §63 append, and only then does a
later stage execute it.

## Part E — Real-data prerequisites after this stage

| Item | Status |
|---|---|
| F-1 / breadth construction | RESOLVED (§62.8) |
| zero-momentum semantics | RESOLVED (§63.1.A.1) |
| exposure-retention gate | RESOLVED (§63.1.A.2, `V_ret ≥ 0.40` under the optimizer's `Σ`) |
| funding observability predicate | confirm §60.11.1 status; list if UNRESOLVED |
| residual-correlation stress test | DELEGATED — fixture+criteria pending D.2 |
| solver pin, determinism | RESOLVED (§61.1) |

State what remains before **trial 1 of 20** may be pre-registered. Do not
pre-register it here.

## Order of work

1. §63.1: Parts A.1–A.4 appended, dated, before code
2. Parts B, C; suites green; manifest delta
3. **D.0–D.5 protocol appended to §63 before the measurement module runs**
4. Measurement; D.2 series and aggregates appended; D.5 test present
5. Part E table; **stop**

## Acceptance

- A.1 as user decision; A.2 as user preference with units, `Σ`-of-record rule,
  and absolute-ceiling companion; A.3 delegated with the development-informed
  naming; **A.4 reordering amendment recorded verbatim**
- Carry label test covers both label conditions
- `V_ret` gate shares the optimizer's covariance object (tested); ceiling test;
  zero-denominator split with fail-closed integrity path; single 0.40 definition
- **D.0 protocol frozen in the ledger before the first return is read**; all
  D.2 objects defined exactly; no narrative statistics; D.4 prohibition
  recorded; D.5 refusal tested
- Stress test not run; **Gen-2 0 of 20; holdout sealed**

## Do not

- Argue, adjust, or re-derive the 40%
- Freeze `V_ret`'s `Σ` to the diagonal model
- Map a nonzero `w_pre` with zero modeled variance to `D_degenerate`
- Compute any performance quantity in Part D
- Add a window, a tail definition, a regime label, or any statistic not in D.2
  after reading data
- Run any strategy component under the measured covariance before the fixture
  is frozen
- Read one day past 2024-12-31; pre-register trial 1
