# Stage 19a (v3) — §60 corrections, contamination-audited

Corrects the contradictions in the RCM v1 specification **without filling any
unresolved tolerance with a plausible-looking number** — including numbers
that survived from an earlier draft after their derivation was withdrawn.

Appended as `NOTES` §60.11 (append-only — §60.0–§60.10 unedited). Gen-2 is
**0 of 20**; no RCM performance result exists.

**No code. No market data, return data, backtest results, or performance
diagnostics.** Repository text, the ledger, and committed exchange metadata may
be used.

§0 of `STAGE2_PROMPT.md`, §59, §60 govern. Holdout sealed.

**Standing principle:** correct contradictions now; leave unresolved
tolerances UNRESOLVED; **the coding agent makes no strategy-definition
decision** — every such item is either explicitly adopted here by the user's
delegate or marked UNRESOLVED for the user.

---

## 1. BLOCKING — Funding forecast: cadence-aware, horizon-matched, no new knob

**Superseded:** §60.2's `F̂ = 3 × mean(last 21 settlements)` and §60.4's
"exactly three settlements" sentence. The ledger established PIT cadence
inference because fixed cadence was wrong.

**Correction.**

```
F̂_i(t) = Σ_{s ∈ Ŝ_i,(t_exec, t+1_exec]}  f̂_i,s
```

- `Ŝ_i` inferred PIT from settlement timestamps known at `t`, **using the
  existing Gen-1 cadence-inference implementation** — Stage 20 does not invent
  a new estimator.
- `f̂_i,s` estimated from **exactly the trailing 7 calendar days** of
  settlements — preserving §60.2's stated weekly-cycle intent, removing only
  its cadence dependence.
- **Observability rule, no minimum-count parameter:** the full trailing
  7-calendar-day window must be certified as sufficiently observed under the
  **existing** funding-cadence / missing-settlement machinery. If it cannot
  be, the candidate is **unavailable for that decision**. Never substitute
  zero funding (Gen-1 precedent: absent funding history ⇒ ineligible,
  otherwise the leg trades cost-free).
- Cadence change mid-lookback: settlement set per PIT timestamps; forward
  schedule from the most recent inferred cadence. State this.

## 2. BLOCKING — Calibration: PIT set-builder AND an exact residual definition

**Superseded:** pooling over all `τ ≤ t`.

**Severity, non-directional:** the fitted slope is contaminated by future
information; its magnitude and sign are not admissible evidence of
predictability, and all derived quantities are invalid for that timestamp.

**2.1 The sample.**

```
D_t = { (τ, i) : outcome_end(τ, i) ≤ decision_cutoff(t) }
```

**2.2 The outcome object — a definition gap closed.** §60.1 defines the factor
model on daily simple returns; §60.4 defines strategy returns on the
00:01→00:01 execution interval. The calibration target must be the
**forward residual on the execution horizon**, e.g.:

```
ε_fwd,i,τ = r_exec,i,τ − β̂_BTC,i,τ · f_exec,BTC,τ − β̂_ETH⊥,i,τ · f_exec,ETH⊥,τ
```

with betas fixed from information available at the signal date, and
`r_exec`, `f_exec` measured over the identical execution-to-execution interval
used by `r_shadow` and `r_actual_price`. The exact formula is Stage 19a's to
record; the requirement is that the calibration outcome and the expected
return it calibrates share **one horizon**, so fixing the timestamp leak does
not leave a close-to-close / open-to-open mismatch behind.

**2.3 Write actual timestamps**, not `t−1/t−2` indexing: at a 00:00:00 UTC
decision on day D with 00:01 execution, the newest admissible outcome interval
is the one ending 00:01 on day D−1 (opened D−2) — the interval opened D−1 has
not finished. Record the exact statement.

**2.4** Stage 20 must include a test that fails if any calibration
observation's `outcome_end` exceeds the decision cutoff.

## 3. BLOCKING — `G_target` undefined and circular

**3.1 Split.** `G_pre = Σ|w_pre,i|` (gate denominator only);
`G_cap = 3.0` (exogenous backstop).

**3.2 Optimizer cap.** The circular `(G_target/2)/6` is invalidated. Adopted:
`|w_i| ≤ G_cap/12 = 0.25` — a **hard safety cap only**, transparently derived
(exact dollar neutrality, gross cap 3 ⇒ each leg ≤ 3/2; one-sixth = 0.25). It
does **not** ensure six-name breadth at low realized gross; the per-leg
`N_eff` gate does that, post-optimizer.

**3.3 Rejected:** the v1 `|w_i|·σ_i ≤ σ_target/√6` cap — recorded with
reasons (whole-portfolio vol target vs per-leg; standalone ≠ marginal risk
under correlation; `σ_i` undefined).

**3.4 Stage-20 synthetic compatibility test — required.** §3.2 creates an
intentional separation: optimizer → possibly-concentrated target → `N_eff`
gate → skip. Stage 20 must construct a synthetic case where a broad,
gate-passing book is clearly feasible and verify the optimizer/quantization
pipeline **produces** a gate-passing book rather than needlessly returning a
concentrated target rejected downstream. Failure = an architecture
incompatibility found before any real return, which is what synthetic testing
is for.

## 4. Correction — `N_eff` interpretation

`N_eff = 1/Σp_i²` is the Herfindahl-equivalent count and bounds no individual
weight. Record `N_eff,leg ≥ 6` (portfolio-level) and `|w_i| ≤ 0.25` (absolute
per-name ceiling, which does **not** imply `p_max ≤ 1/6`) as complementary and
non-equivalent.

## 5. Correction — residual independence; stress test deferred; overinterpretation fenced

**5.1** Relabel independent-error `SE(β_kᵀw)` and diagonal `D` as
**approximations** — orthogonality to BTC/ETH is not cross-coin independence.

**5.2** With `z = 1.645` on the absolute constraint, nominal two-sided coverage
is 90% and nominal total breach is **10%**.

**5.3** Stage 20 must pre-register **fixture (correlation strength and
structure) and failure criteria together, before executing** the synthetic
correlated-residual test. **No tolerance is frozen here.** Neither may change
after the result. Failure ⇒ residual covariance treatment before any real
data.

**5.4** Record: passing the declared synthetic fixture establishes robustness
**only to that pre-registered scenario**; it does not establish that diagonal
residual covariance is adequate in the real market.

## 6. `g_min` — WITHDRAWN, UNRESOLVED

The only derivation `g_min` ever had was `g² ≥ 0.5` under `w_real = g·w_pre`.
Feasibility drops and quantizes names, so the identity fails and the
derivation is invalid. **Both `0.70` and its proposed replacement `√0.5` are
withdrawn.** Renaming the same number an "exposure-retention threshold" does
not supply a derivation.

`g_min` is **UNRESOLVED**: fixed before any real-data run, via either (a) a
non-performance rationale for a gross-retention invariant, or (b) replacing
the gate with a direct predicted-variance-retention measure using the
existing covariance model — (b) is an architecture change and gets its own
derivation. Stage 20 implements the gate with a **symbolic/configured
threshold** and no default value.

## 7. `S_i` — ADOPTED by the user's delegate, not the agent

`S_i = |μ_mom,i|`. Rationale: signal coverage exists to detect feasibility
discarding *the hypothesis*; using total `|μ_i|` would let a book that keeps
funding-rich names and drops momentum names score high coverage while
abandoning residual momentum. This is a strategy-definition decision made
explicitly here, vetoable by the user; the coding agent records it and does
not re-decide it.

## 8. Zero-denominator semantics — split

**8.1 `G_pre = 0` ⇒ `degenerate_target`**, a named deterministic state placed
in the §60.6 causal-precedence order at the optimizer/feasibility boundary,
classified into exactly one calendar category.

**8.2 Zero signal-mass denominator (`Σ|w_pre||μ_mom| = 0`) ⇒ UNRESOLVED —
escalated to the user.** With `S_i = |μ_mom|`, this case is exactly *momentum
has vanished, only funding remains*. §60.2.3 pre-registered the carry regime
as **flag and report — never a halt**. Converting the zero denominator into
`degenerate_target` would silently change that to "carry regime ⇒ do not form
book," a strategy-layer change. The two options, for the user:

- **(a)** consistent with §60.2.3: coverage is *not applicable* when there is
  no momentum mass to retain; the book may form as a flagged CARRY REGIME
- **(b)** a deliberate amendment making the carry regime a halt

**Delegate's recommendation: (a)** — it preserves the rule already on record.
Until the user decides, Stage 20 treats this state as `UNRESOLVED` (raise,
do not choose). NaN never decides strategy state; neither does a side effect.

## 9. Ledger integrity — manifest delta

§60.11 ends with a supersession table; §60.9 is not modified:

```
funding forecast     | SUPERSEDES §60.9 row       | §60.11.1
calibration sample   | SUPERSEDES §60.9 row       | §60.11.2
optimizer name cap   | SUPERSEDES §60.9 row       | §60.11.3
g_min                | WITHDRAWN — UNRESOLVED     | §60.11.6
S_i                  | NEW — adopted by delegate  | §60.11.7
zero momentum mass   | UNRESOLVED — user decision | §60.11.8
stress thresholds    | UNRESOLVED — Stage 20 pre-registers | §60.11.5
```

---

## Order of work

1. Append `NOTES` §60.11 with §1–§9, dated; §1–§3 marked blocking
2. Confirm §60.0–§60.10 byte-identical
3. Report, listing every UNRESOLVED item and the one user decision pending

## Acceptance

- §60.11 appended; earlier sections unedited; manifest delta present
- Funding: PIT cadence via existing implementation, exact 7 calendar days,
  full-window observability rule, no count parameter, §60.4 sentence
  superseded
- Calibration: set-builder, execution-horizon residual defined, actual
  timestamps written, Stage-20 test required
- `G_pre`/`G_cap`; 0.25 cap with honest scope; `σ/√6` rejected; §3.4
  synthetic compatibility test required
- `N_eff` corrected; residual independence approximate; 10% nominal; stress
  fixture+criteria deferred; §5.4 fence recorded
- `g_min` withdrawn and UNRESOLVED with symbolic implementation
- `S_i` adopted by delegate with rationale; zero-momentum case UNRESOLVED and
  escalated with both options and the recommendation
- `degenerate_target` for `G_pre = 0`
- **No code, no market/return/performance data. Gen-1 15 of 25; Gen-2 0 of 20.
  Holdout sealed.**

## Do not

- Keep any number whose derivation has been withdrawn
- Let the coding agent decide `S_i`, the zero-momentum semantics, or `g_min`
- Introduce a minimum settlement count or a new cadence estimator
- Convert the carry regime into a halt by side effect
- Freeze any stress tolerance here
- Edit §60.9 or any existing section
- Write code, access data, or touch the holdout
