# Stage 19c (v2) — §62 amendment: leg membership must be shift-invariant

One mathematical correction to the Stage 19b construction, one new test, one
semantic broadening, wording precision. Appended as `NOTES` §62.8 (append-only;
§62.0–§62.7 unedited).

**No real market or return data. Gen-2 stays 0 of 20.** Holdout sealed.
§0 of `STAGE2_PROMPT.md`, §59, §60, §60.11, §62 govern.

**Status discipline for this stage: specified ≠ verified.** Nothing below is
marked RESOLVED until §3 passes and the result is appended.

---

## 1. The bug

§62.2 assigns leg membership by raw `sign(μ_i)`. The optimizer is exactly
dollar-neutral, `1ᵀw = 0`, so for any constant `c`:

```
(μ + c·1)ᵀw = μᵀw + c·1ᵀw = μᵀw
```

The economic problem is invariant to a common shift of all expected returns.
Raw `sign(μ_i)` is not. Example: `μ = (−0.02, −0.01, +0.01, +0.02)` admits two
longs and two shorts; `μ + 0.03·1 = (0.01, 0.02, 0.04, 0.05)` is economically
identical but assigns every name long-only, and neutrality forces `w = 0`. The
construction's output depends on an arbitrary zero level of the forecast.

**1.1 Carry-regime interaction.** In the zero-momentum case
`μ_total,i = −F̂_i`. In the case where all funding rates are positive, raw sign
makes every name short-only and the labelled CARRY REGIME book of §60.11.8(a)
cannot form. The algebra stands on its own; no empirical claim about typical
funding is made or needed.

## 2. The correction — a derivation, not a rule

Let `P = I − (1/N)·1·1ᵀ`. `P` is the **orthogonal projection onto the
dollar-neutral subspace** `{x : 1ᵀx = 0}` under the Euclidean inner product.
For every feasible dollar-neutral `w`:

```
μᵀw = (Pμ)ᵀw
```

So `μ̃ = P·μ_total` is **the canonical component of expected return that can
affect a dollar-neutral portfolio**; the discarded component `(1/N)(1ᵀμ)·1`
lies in a direction the feasible set is mathematically insensitive to.
Mean-centering is therefore not one shift-invariant choice among several
(median-centering is also shift-invariant) — it is the unique projection
implied by the existing constraint. Any other center would retain a component
the optimizer cannot see and discard one it can.

```
μ̃_i = μ_total,i − μ̄_total          μ̄ = (1/N) Σ_i μ_total,i
μ̃_i > 0 ⇒ L        μ̃_i < 0 ⇒ S        μ̃_i = 0 ⇒ excluded
```

No coefficient, no threshold, no return-data selection, no trial.

**2.1 Total `μ`, not `μ_mom`.** Membership uses `μ_total = μ_mom − F̂`
(§60.2/§60.11.3.3). With `μ_mom = 0`, `μ̃_i = −F̂_i + F̄`: above-average funding
goes short (collecting it), below-average goes long; under neutrality the
common funding level has no economic value — only the cross-sectional
difference does. The carry book is well-defined.

**2.2 Ordering, frozen — no recursive membership.**

```
PIT structural eligibility → funding/data eligibility → μ_total
→ μ̄ (over that eligible set) → μ̃ → sign partition → optimizer
```

The center is computed **once** over the eligible set at that point. No name
is removed because of its centered sign and the mean recomputed — that would
be a hidden iterative selection rule.

## 3. Tests — verification, after which (and only after which) F-1 is RESOLVED

**3.1 Common-shift invariance.** Solve a synthetic instance for `μ` and
`μ' = μ + c·1`, for both `c > 0` and `c < 0`, each large enough to flip several
raw signs, and at least one case with nonzero `w_prev` (turnover is in the
objective; the shift must still cancel). Assert `w(μ') = w(μ)` within the
frozen solver tolerance and identical membership. **Run first against the old
raw-sign path and record that it fails**; then against the centered path and
record that it passes; then remove the old path.

**3.2 Sign agreement, re-based** to `sign(μ̃_i)`.

**3.3 Carry-regime formation.** `μ_mom ≡ 0`, all `F̂_i > 0` (varying): the
construction forms a labelled CARRY REGIME book with the §2.1 sides — not
`D_degenerate`. (If §60.11.8(a) is not yet confirmed, assert the construction
would form it and the path raises UNRESOLVED downstream.)

**3.4** All §62.7 fixtures re-run green; every frozen quantity unchanged.

## 4. `D_degenerate` — semantics broadened (supersedes §62.4's gloss)

§62.4 glossed a zero target as "expected returns net of costs do not justify
exposure." Under the new construction `w = 0` also arises from valid
constraints interacting: e.g. 4 positive and 26 negative centered names — the
long leg cannot reach `N_eff ≥ 6`, neutrality zeroes the short leg; or the
chance constraint throttling the sign-restricted book (§62.7). Amend:

> **`D_degenerate` = the optimizer stage completed without structural or
> operational failure but produced no meaningful nonzero admissible target.
> This may arise from economic no-trade OR from the interaction of valid
> portfolio constraints. It must not be interpreted by itself as evidence that
> expected alpha was zero.**

No new category, no threshold. Attribution may not say "the model saw no
opportunity" for a `D_degenerate` day without decomposing which cause applied;
record the cause (`no_trade` vs `constraint_interaction`, with the binding
constraint named) as a field on the day.

## 5. Wording corrections (append; do not edit §62.2)

- "the ONE admissible candidate" → "the simplest admissible candidate within
  the retained convex SOCP architecture" (mixed-integer complementarity is
  another coefficient-free route, rejected for complexity, not proven
  nonexistent).
- "hold a name only on the side its residual momentum indicates" → "hold a
  name only on the side indicated by its cross-sectional total expected-return
  advantage, after momentum and funding are combined."

## 6. Status after this amendment

**Before §3 runs:** Stage 19c specifies the proposed structural resolution of
F-1. **After §3.1–§3.4 pass and the result is appended:** F-1 is RESOLVED and
the breadth-construction chain is closed — not revisited unless another
synthetic invariant breaks. Still blocking trial 1 of 20, unchanged: `g_min`
(§60.11.6), zero-momentum semantics (§60.11.8), residual-correlation robustness
(§62.5) — awaiting the user's risk preferences.

## Order of work

1. Append `NOTES` §62.8 with §1, §2, §4, §5 and the §6 pre-verification status;
   confirm §62.0–§62.7 byte-identical
2. Implement centered membership per §2.2 ordering; §3.1 fail-old / pass-new
   recorded; old path removed
3. Re-run all suites; append the §3 verification result; only then mark F-1
   RESOLVED in §62.8

## Acceptance

- §62.8 appended with the projection derivation and frozen ordering
- Membership by `P·μ_total`; `μ̃_i = 0` excluded; center computed once
- §3.1 shown failing on the old path, passing on the new, both signs of `c`,
  a nonzero-`w_prev` case; §3.2 re-based; §3.3 present; §3.4 green with frozen
  quantities unchanged
- `D_degenerate` semantics broadened; cause field recorded per day
- F-1 marked RESOLVED only in the post-verification append
- **No real data; Gen-2 0 of 20; holdout sealed**

## Do not

- Mark F-1 resolved before §3 passes
- Center by anything other than `P·μ_total` (the projection, not a preference)
- Recompute the mean after removing centered-sign names
- Use `μ_mom` for membership
- Attribute a `D_degenerate` day to "no alpha" without its recorded cause
- Edit §62.2; touch real data or the holdout
