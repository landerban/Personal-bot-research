# Stage 22 — §63.6 joint append, and the residual covariance estimator (synthetic)

Two parts. **Part I** appends the delegates' converged decision as `NOTES`
§63.6 — the user delegated the residual-correlation robustness requirement
(§63.1.A.3); both reviewers now agree on its form. **Part II** implements the
resulting estimator and tests it synthetically. **No real returns are read by
any Part II test.** Gen-2 stays **0 of 20**. Holdout sealed.

§0 of `STAGE2_PROMPT.md`, §59, §60, §60.11, §62, §62.8, §63 govern.

---

# PART I — §63.6 JOINT APPEND (delegates' decision, dated)

## I.1 The eight fixture-construction corrections — recorded

1. `diag(Ω_true) = D`; 2. `Σ_true = BΣ_fBᵀ + Ω_true`; 3. total-trace shares
`(0.3067, 0.0445, 0.0368)` from §63.3 medians, `_ex1` quantities are
secondary-spectrum diagnostics only; 4. any p95 combination is a
*componentwise p95 spectral envelope*; 5. stress modes hedge-orthogonal and
weight-agnostic — only the `1` direction is exactly annihilated, betas are
chance-bounded; 6. analytic beta coverage
`SE_true,k²(w) = [(XᵀX)⁻¹]_kk · wᵀΩw`; 7. per-instance grading, tail
informational, seeds derived from frozen F-1 seeds; 8. pairwise-correlation
comparison is a distortion report.

## I.2 Synthetic design diagnostic — the random-orientation gate cannot judge

Recorded as a **design diagnostic, not execution of the deferred stress test**:
with correction #1 enforced and modes uniform in the hedge-orthogonal
complement (`N = 117`, median shares, unit-variance `D`, 4,000 orientation
draws), `wᵀΩ_true w / wᵀDw` has mean 1.01, median 0.86, std 0.46;
`P(ratio > 1) = 34%`; `P(all six base instances pass) ≈ 8%`.
`E[wᵀΩ_true w] ≈ wᵀDw`: the fixture is trace-neutral for a weight-agnostic book
and adds only orientation scatter.

**Provenance — this diagnostic supports a specification change, so it is
reproduced in-repo, not left as four numbers in prose.** Record exactly what
was computed, including its simplifications:
- `numpy.random.default_rng(0)`; 4,000 draws; `N = 117`; total-trace shares
  `(0.3067, 0.0445, 0.0368)`; `D = I` (unit residual variances)
- **Books:** random standard-normal vectors, demeaned (dollar-neutral) and
  unit-normalized — **not the six F-1 books**. The "all six pass ≈ 8%" figure
  is `(1 − 0.34)⁶` under an independence assumption, **not** a measurement on
  the F-1 instances.
- **Modes:** three standard-normal vectors, demeaned, QR-orthonormalized — the
  complement of `span{1}` **only**; the beta directions were omitted for
  illustration.
- `C = Σ_k λ_k q_k q_kᵀ + diag(1 − diag(·))`; statistic `wᵀCw` vs `wᵀDw = 1`.
- Stage 22 reproduces this as `research/orientation_diagnostic.py` with the
  seed fixed, records the script hash in §63.6, and **may** additionally run
  it on the actual six F-1 books with the full `span{1, β}` complement as an
  informational extension — the analytic trace-neutrality argument does not
  depend on which book, but the ledger should not imply the F-1 books were
  used when they were not.

**Statement, narrowed:**

> A tolerance-free, weight-agnostic random-orientation fixture **cannot support
> a deterministic PASS/FAIL judgment about covariance adequacy**; it can only
> characterize sensitivity to unoriented misspecification. Criterion (ii)
> inherits the same property through `wᵀΩ_true w` in `SE_true`.

**Decision:** the random-orientation PASS/FAIL gate of §60.11.5 / §62.5 is
**withdrawn as unable to render a verdict** — neither passed nor failed.

## I.3 The finding — stated to the evidence, not beyond it

> The diagonal residual-covariance assumption is **strongly and pervasively
> contradicted** by the development structural measurement under the
> registered spherical null (§63.5: median `R_λ1 = 7.625`, p5 `3.128`;
> `R_F` median 2.924). Retaining it as the sole RCM v1 residual-risk model is
> therefore **not supported by the evidence available before freeze.**

Not claimed: that `D` is misspecified "at every date" (the `m_eff < 87`
caveat stands; a sample covariance is not the population covariance).

## I.4 The specification change — residual factor covariance, exact

Pre-freeze (§59.8: no version bump). Development-informed (§63.1.A.3.1 caveat
travels with it: never citable as independent evidence of generalization).
**Estimation, not selection (§60.0):** fully pre-registered, PIT, never
compared to the diagonal model on any performance quantity.

**Universe, frozen:** correlation estimation runs on the **PIT risk-eligible
set before any momentum score, expected-return sign, portfolio weight, gate
outcome, or performance conditioning** — the same set and complete-case
alignment as §63.2.

**Estimator, in correlation space:**

```
D_t   = diag(σ̂²_ε,1 … σ̂²_ε,N)             — the residual variances RCM already uses
C_t   = Q_t Λ_t Q_tᵀ                        — 90-day residual sample correlation, λ₁ ≥ … ≥ 0
λ_+,t = (1 + √(N_t / 87))²                  — MP edge in raw eigenvalue units (≡ s_MP+ · N_t)
K_t   = #{ j : λ_j,t > λ_+,t }              — rank selected by the preregistered MP-edge rule; no fixed K chosen
L_t   = Σ_{j ≤ K_t} λ_j,t q_j,t q_j,tᵀ
r_t   = 1 − diag(L_t)
C_tᴿᶜᴹ = L_t + diag(r_t)
Ω̂_t   = D_t^{1/2} C_tᴿᶜᴹ D_t^{1/2}
```

**Properties, proven in the append:**
- `diag(Ω̂_t) = diag(D_t)` — marginals preserved exactly; only correlation
  structure is added.
- **PSD:** `L_t` is a subset of the nonnegative eigencomponents of `C_t`, so
  `C_t − L_t ⪰ 0`, hence `1 − diag(L_t) = diag(C_t − L_t) ≥ 0`; both terms of
  `C_tᴿᶜᴹ` are PSD, so `Ω̂_t ⪰ 0`.
- **`K_t = 0 ⇒ C_tᴿᶜᴹ = I ⇒ Ω̂_t = D_t`** — the diagonal model is the automatic
  boundary case; no fallback rule.
- **Raw spikes, frozen:** eigenvalues above the edge are retained at their
  observed sample values; **no shrinkage, de-biasing, clipping, or
  performance-selected regularization** in RCM v1. The MP-edge rule is an
  **estimator choice** — pre-registered and not performance-selected, but a
  choice, and recorded as one.
- `m_eff < 87` (§63.5.4) may inflate `K_t` by one on some dates — recorded,
  not corrected.

**Floating-point policy, frozen (numerical hygiene, not regularization):**
- Symmetrize before decomposition: `C ← (C + Cᵀ)/2` prior to `eigh`.
- Remainder: analytically `r_i ≥ 0`. If `r_i < −SOLVER_TOL` (the already-frozen
  §60.7 numerical tolerance) ⇒ **covariance integrity failure**, fail closed,
  `D_structural`, alert. If `−SOLVER_TOL ≤ r_i < 0` ⇒ set to exactly zero as
  **numerical zero-clean only**, with the maximum correction recorded per date.
  This removes floating-point violations of an analytically nonnegative
  quantity; it is **not** statistical eigenvalue clipping and introduces no
  model choice.

**Downstream, consistent:**
- `Σ_model = BΣ_fBᵀ + Ω̂_t` in the optimizer and in `V_ret` (§63.1.A.2.1: one
  risk model, two uses).
- Chance constraint: `SE_k²(w) = [(XᵀX)⁻¹]_kk · wᵀΩ̂_t w`, replacing the
  independent-error `Σ_i w_i² V_i[k,k]` whose assumption §60.11.5 flagged.

## I.5 Estimation uncertainty — a limitation, not a gate

> Estimation uncertainty in `K_t`, retained eigenvalues, and loading vectors
> remains a **model-risk limitation of RCM v1**. No additional robustness
> margin is introduced by the delegates. Any future variance buffer or
> uncertainty-set tolerance (e.g. `wᵀΣ̂w ≤ σ²/c`, `c > 1`) is a **risk-owner
> decision** and constitutes a separately governed specification change.

The 10% target was always a predicted quantity; no finite-window estimator
guarantees ex-post variance. The delegates do not manufacture a prerequisite
from that fact.

## I.6 Governance closures
- §60.11.5 / §62.5 stress requirement: **closed by withdrawal and model
  change**, status DELEGATED → RESOLVED.
- §63.2 D.4 prohibition: **superseded** — it guarded against choosing a gate's
  criterion around its answer; there is no gate. PIT estimation of `Ω̂_t` on
  development data is the model's normal operation.
- No loading measurement for design purposes occurred or is authorized.

# PART II — Implement and test the estimator (synthetic; no real returns)

## II.1 Module
`rcm/rescov.py` implementing I.4 verbatim; wired into `Σ_model`, `V_ret`, and
the chance-constraint `SE`. Import-level test: the synthetic suite reaches no
real-data reader.

## II.2 Tests

| Claim | Fixture |
|---|---|
| Marginals | `diag(Ω̂) = diag(D)` to numerical tolerance on random `C`, `D` |
| PSD | smallest eigenvalue of `Ω̂ ≥ −tol` on adversarial `C` (near-rank-deficient, `N > 87`) |
| `K = 0` boundary | a constructed `C` with all eigenvalues below `λ_+` ⇒ `Ω̂ = D` exactly (deterministic) |
| Null behaviour (informational) | spherical synthetic residuals, 90 obs: report the empirical distribution of `K_t` — `P(K_t = 0)`, `E[K_t]`, frequency of eigenvalues above the edge. **No pass/fail frequency criterion**: the MP edge is an asymptotic reference, not a finite-sample critical value (§63.5.2) |
| Rank rule — deterministic | construct a PSD correlation matrix with known eigenvalues `λ_1..λ_k > λ_+ > λ_{k+1}..`; assert exactly `K = k` and that `L` contains exactly those components. Proves the implementation obeys the specification |
| Rank recovery — stochastic (informational) | generate 90 observations from known low-rank population structures; report empirical detection/recovery of `K` and loadings (sign/rotation tolerance). **No exact-`K` acceptance criterion** — population spikes and sample eigenvalues are different objects and sampling moves them |
| No shrinkage | retained `λ_j` equal sample values bit-for-bit |
| Floating-point policy | a near-rank-deficient fixture producing `r_i ∈ [−SOLVER_TOL, 0)` is zero-cleaned with the correction recorded; one producing `r_i < −SOLVER_TOL` fails closed as integrity failure; symmetrization applied before `eigh` |
| Universe ordering | changing momentum scores / weights / gates does **not** change `C_t`, `K_t`, `Ω̂_t` (a test permutes downstream inputs and asserts the estimator's output is invariant) |
| **Neutrality, corrected** | plant a residual factor with **covariance-space** loading `a = c·1`; assert its low-rank term `aaᵀ` contributes **zero** to `wᵀ(aaᵀ)w` for every exactly dollar-neutral test `w`; do **not** assert the full marginal-preserving `Ω̂` contributes zero. Second fixture: `q ∝ 1` in **correlation** space with heteroskedastic `D` ⇒ `a = D^{1/2}q ∦ 1` and `aᵀw ≠ 0` for generic neutral `w` — documents why empirical modes belong in the model |
| Chance constraint | `SE_k²(w) = [(XᵀX)⁻¹]_kk wᵀΩ̂w` reproduces the independent-error formula exactly when `K = 0` (since `wᵀDw = Σ_i w_i²σ²_ε,i`), and exceeds it when a planted mode has `aᵀw ≠ 0` — a strict generalization, not a second risk calculation |
| Determinism | same inputs ⇒ same `Ω̂` within §60.7 tolerance; eigenvector sign convention fixed and tested |
| F-1 and §62.8 fixtures — **invariants, not outcomes** | re-run under `Ω̂`. **All frozen construction semantics and invariants must hold:** `1ᵀw = 0`; centered-sign membership (§62.8); `N_eff,L/S ≥ 6` on any book that passes; `|w_i| ≤ 0.25`; chance constraint evaluated with the new `SE`; `wᵀΣ_model w ≤ σ²`; `D_degenerate`/gate causal precedence; common-shift invariance. **Numerical weights, gross, `V_ret`, gate outcomes, and formation status are permitted to change** where the new covariance legitimately changes modeled risk — requiring identical outcomes would force the new risk model not to matter |
| Reporting | the daily tuple gains `K_t` and `λ_1/tr`; the §63.5 null references appear beside them |

## II.3 What this stage does not do
- Compare `Ω̂` to `D` on any performance quantity
- Read real returns in any test
- Pre-register trial 1

## Order of work
1. §63.6 Part I appended, dated, with the PSD proof and both wording rulings
2. `rcm/rescov.py`; II.2 tests green; all prior suites green
3. Report; then the trial-1 prerequisites table (Part E of §63.4, updated):
   every row RESOLVED or the specific residual named

## Acceptance
- §63.6 contains I.1–I.6 verbatim in substance; §63.0–§63.5 unedited;
  orientation-diagnostic provenance recorded incl. its simplifications and
  script hash
- Estimator matches I.4 exactly; PSD/marginal proofs recorded; raw-spike,
  pre-alpha-universe, and floating-point policies recorded
- All II.2 fixtures present: deterministic tests green; informational
  diagnostics reported with **no** frequency or exact-`K` pass/fail
- F-1/§62.8 re-run preserves the listed **invariants**; outcome changes
  recorded, not forbidden
- Corrected neutrality test and ordering-invariance test present and green
- Chance constraint and `V_ret` use `Ω̂_t`; single covariance object (tested)
- I.5 recorded as limitation, no new blocker; D.4 superseded
- **No real returns read; Gen-2 0 of 20; holdout sealed**

## Do not
- Apply any eigenvalue shrinkage, clipping, or de-biasing (zero-clean within
  `SOLVER_TOL` is numerical hygiene, not this)
- Choose `K` by anything but the registered MP-edge rule
- Estimate `C_t` on any set conditioned on alpha, weights, or gates
- Treat the MP edge as a false-positive threshold, or exact-`K` recovery from
  90 sampled observations as a unit-test criterion
- Require F-1/§62.8 **outcomes** to be unchanged under `Ω̂`
- Assert the false equal-`q` neutrality claim
- Imply the orientation diagnostic used the F-1 books or the full
  `span{1, β}` complement when it did not
- Introduce a variance buffer or robustness margin
- Read real returns, or pre-register trial 1, in this stage
