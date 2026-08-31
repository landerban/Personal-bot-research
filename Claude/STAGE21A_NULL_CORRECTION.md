# Stage 21a — Analytic null correction to §63.3 (no data read)

The §63.3 measurement compared eigenvalue shares to `1/N_t` and Frobenius
distance to zero. Both are **population identity** references, not the
finite-sample independence null. With `T = 90` observations and residuals from
a three-regressor OLS (`1`, `f_BTC`, `f_ETH⊥`), every residual vector lies in
the same `m = 87`-dimensional subspace; the sample correlation matrix is
**rank-deficient by construction** for `N_t > 87`, and its spectrum is uneven
under independence.

This stage appends the correct null **analytically**, from quantities already
in `diagnostics.jsonl` (`N_t`, `T`, regression dimension). **It reads no
returns.** It adds no new statistic to the data; it corrects the benchmark
against which already-recorded statistics are read.

Append as `NOTES` §63.5. Gen-2 **0 of 20**. Holdout sealed.

---

## 1. Correct the wording (append; do not edit §63.3)

> `1/N_t` is the **population identity share**, retained only as a population
> reference. Because each matrix is estimated from 90 observations after three
> regression degrees of freedom (`m = 87`), finite-sample spectral
> concentration under independence is materially larger; the analytical
> `m = 87` independence null is reported separately (§63.5).

## 2. Per-date analytic null references (computed from `N_t` only)

```
m = T − 3 = 87

Marchenko–Pastur upper edge share:
  s_MP+(N_t) = (1 + √(N_t / m))² / N_t

Frobenius RMS null scale:
  F_RMS,null(N_t) = √( N_t (N_t − 1) / m )
```

**Derivation of `F_RMS,null`.** Under the spherical independence null, two
independent normalized residual vectors in the common `m`-dimensional residual
subspace have `E[ρ_ij²] = 1/m`, hence `E[‖C − I‖_F²] = N(N−1)/m`. The quantity
above is therefore `√E[‖C−I‖_F²]` — a **root-mean-square null scale**, not
`E[‖C−I‖_F]` (since `E[√X] ≠ √E[X]`). Name it accordingly.

**Character of the MP edge.** The Marchenko–Pastur upper edge is an
**asymptotic** reference under a spherical Wishart independence model as
`N, m → ∞` with `N/m` fixed; at `N ≈ 117, m = 87` it is an analytical
approximation, **not an exact finite-sample quantile and not a hypothesis-test
critical value** — `λ_max` fluctuates around it under the null.

Worked values, `N_t = 117`: `s_MP+ ≈ 0.0399` (vs `1/N = 0.0085`);
`F_RMS,null ≈ 12.49`.

## 3. Ratios, appended per date from the existing file

```
R_λ1 = eig1_share / s_MP+(N_t)
R_F  = ‖C − I‖_F / F_RMS,null(N_t)
```

Report the same distributional summary (p5/p25/p50/p75/p95 across dates) as
§63.3. Expected from the recorded medians: `R_λ1 ≈ 0.307 / 0.0399 ≈ 7.7`,
`R_F ≈ 38.8 / 12.49 ≈ 3.1`.

State the conclusion in exactly this form:

> **The spherical finite-sample cross-sectional independence null is strongly
> contradicted; the magnitude of the excess is now correctly benchmarked
> against the `m = 87` analytical null.**

Record what that null assumes and what it does not cover: it is a
spherical/i.i.d. residual model conditional on the common regression design.
Temporal autocorrelation or heteroskedasticity in idiosyncratic residuals would
reduce the effective temporal sample size (`m_eff < 87`) and widen the
independence spectrum **without any cross-asset dependence**. This stage does
not estimate `m_eff` — doing so would require a new assumption or a new
data-derived statistic and exceed the amendment's scope. The distinction
*cross-sectional dependence ≠ temporal dependence* is recorded for the
delegates and left open.

## 4. What the correction does and does not change

- Does not change the qualitative finding: cross-asset residual independence
  is strongly contradicted by development data.
- Does not soften it: the finite-sample MP spectral reference is ~4.7× the
  population identity share at the representative `N = 117`, while the
  measured leading-mode concentration remains ~7.7× the MP edge. There is no
  single scalar "corrected null" spanning all diagnostics — each statistic has
  its own reference (`s_MP+` for the spectrum, `F_RMS,null` for the Frobenius
  distance).
- Does change the *quantitative* language available to the delegates for
  fixture design — they now compare against a valid null.
- Does not, by itself, say the portfolio is unsafe: dollar and factor
  neutrality may cancel part of the common structure. That is the stress
  test's question, still unrun.

## Acceptance

- §63.5 appended; §63.3 unedited; wording correction present
- `s_MP+`, `F_RMS,null` (with its `E[ρ²] = 1/m` derivation and RMS naming),
  `R_λ1`, `R_F` per date and summarized; derived from `diagnostics.jsonl`
  only (import-level test: no data readers)
- MP edge described as asymptotic reference, not a critical value
- Conclusion names the **spherical** null; temporal-dependence caveat recorded,
  `m_eff` not estimated
- No new statistic computed from returns; no optimizer/gate/PnL code touched
- Gen-2 0 of 20; holdout sealed

## Do not

- Read any return data
- Add any statistic beyond §2–§3 — no `m_eff` estimator, bootstrap,
  alternative random-matrix null, or temporal-dependence measure
- Call the MP edge a critical value, or `F_RMS,null` an expected norm
- Run any strategy component under any covariance
- Freeze the stress fixture here — that is the delegates' joint append
