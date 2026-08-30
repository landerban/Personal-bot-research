# Stage 19 — RCM v1 mathematical specification

The hardest document in the project. Derives every quantity RCM v1 needs —
model, calibration, constraints, gates, thresholds, state transitions,
timeline — **without looking at which choice makes more money.**

**No optimizer code. No market data, return data, backtest results, or
performance diagnostics.** Repository text, the ledger, exchange metadata
already committed, and pure algebra/synthetic fixtures may be used.

§0 of `STAGE2_PROMPT.md` and §59 (incl. §59.11) govern. Gen-1 frozen, 15 of 25.
Holdout sealed. **Gen-2 budget: 0 of 20 — this stage consumes none.**

Append as `NOTES` §60, dated.

---

## 0. The standing rule for this entire stage

Every number and functional form is justified by **architecture, economics, or
arithmetic**. None may be chosen by comparing performance.

**Estimation is not selection.** Fitting parameters from data by a
pre-registered PIT procedure — betas, covariances, the momentum-to-return
calibration — is *estimation*, is legitimate, and consumes no trial. Choosing
*among candidate procedures* by comparing Sharpe, PnL, drawdown or formation
rate is *selection* and is forbidden here. State this distinction in §60 so it
is not later misread as a prohibition on estimating anything.

---

## 1. The factor model and the collinearity problem

**1.1** Specify the residual construction:

```
r_i,t = α_i + β_BTC,i · f_BTC,t + β_ETH⊥,i · f_ETH⊥,t + ε_i,t
```

**1.2 ETH must be orthogonalized against BTC.** BTC and ETH returns are highly
correlated; raw two-column OLS yields unstable, sign-flipping coefficients.
Freeze `f_ETH⊥ = ETH return residualized on BTC return` (pre-registered
estimation window), so the two factors are orthogonal by construction. State
the window and the estimator; justify the window structurally (data
availability, half-life of beta drift), not by fit quality.

**1.3** Freeze the beta estimator: method, window, weighting, and the
covariance `V_i` of `β̂_i = [β̂_BTC,i, β̂_ETH⊥,i]ᵀ`. `V_i` is required by §3.

## 2. Momentum in expected-return units — the hardest item

§59.3.3 requires `μ_total = μ_mom − F` with λ=1. That is only meaningful if
`μ_mom` is a **return**, not a z-score. Resolve it.

**2.1 The calibration.** Pre-register one PIT procedure converting the residual
momentum score to an expected return, e.g. a rolling cross-sectional
predictive regression

```
r_res,i,t+1 = a_t + b_t · Z_mom,i,t + ε      →      μ̂_mom,i,t = b_t · Z_mom,i,t
```

Freeze: window, estimator, weighting, winsorization, and how `b_t` is
initialized before enough history exists. This is **estimation**, per §0.

**2.2 `b_t` instability must be handled in advance.** A cross-sectional slope
estimated on noisy returns will be unstable and will change sign. Pre-register
a shrinkage rule or a floor (e.g. shrink `b_t` toward a long-run prior with a
frozen intensity). Derive the shrinkage from estimation-error arithmetic, not
from which setting performed better.

**2.3 CARRY-DEGENERATION GUARD — mandatory.** If `b_t → 0`, then `μ_mom → 0`
and `μ_total ≈ −F`: **RCM silently becomes a pure carry trade.** Gen-1 was
exactly this and discovered it only after the fact (60% of PnL from funding).
Pre-register:

- a standing attribution reported **every run**: the share of `|μ_total|`
  contributed by `μ_mom` versus `F`, cross-sectionally and over time;
- a pre-registered threshold beyond which the book is **declared a carry
  strategy, not RCM**, and the run is flagged as such;
- the consequence of crossing it (flag and report — not a silent continuation).

**2.4 The funding forecast.** Freeze the expected-funding estimator and its
horizon, matched to the holding horizon in §4. State the sign convention
explicitly: positive funding **costs a long and pays a short** — the error
§58 caught.

## 3. Beta uncertainty belongs in the constraint, not the alpha

**3.1 Reject the reliability-multiplier form.** `R_i = β²/(β²+SE²)` conflates
"beta is small" with "beta is uncertain" — a precisely-estimated near-zero beta
is *ideal* for neutral construction, yet that form penalises it. Record the
rejection and the reason.

**3.2 Use an uncertainty-aware neutrality constraint.** Replace `β̂ᵀw = 0` with
a chance constraint per factor (and jointly, using `V_i`):

```
|β̂ᵀw| + z · SE(βᵀw) ≤ ε_β        where  SE(βᵀw) = sqrt(wᵀ V w)
```

This makes uncertain hedges a continuous **risk cost** without pretending beta
uncertainty says anything about the momentum signal.

**3.3 Derive `z` and `ε_β`.** Both fall under §59.4.4: derived from the
intended risk architecture (what residual factor exposure is tolerable at the
target volatility, at what confidence), **never swept**. Show the arithmetic.

## 4. The event timeline — freeze it exactly

Δ_transition subtracts `r_shadow` from `r_actual_price`; if the two are
measured over different holding intervals the difference is a timing artifact
masquerading as a transition effect (the Gen-1 millisecond-funding class of
error). Freeze, with exact UTC times:

```
data cutoff → signal computation → optimizer → feasibility gates
            → execution (entry) → mark → attribution horizon
```

State: decision timestamp, executable entry time, mark time, the holding
interval used for **both** `r_shadow` and `r_actual_price` (identical by
construction), and the funding-accrual window aligned to it.

## 5. The gates — bounded formulas and derived thresholds

**5.1 Signal coverage, corrected.** The §59.4 wording admits values > 1 if
survivors are renormalized. Freeze the bounded form using **pre-feasibility
weights of surviving names**:

```
C_signal = Σ_{i ∈ survive} |w_pre,i| · |S_i|  /  Σ_i |w_pre,i| · |S_i|     ∈ [0,1]
```

Record this as a **correction to §59.4's formula** (append; do not edit §59).
Note the orthogonality: `C_signal` = *which alpha was retained*;
`G_realized/G_target` = *how much exposure was retained*.

**5.2 Per-leg effective breadth.** `N_eff = (Σ|w_i|)²/Σw_i²` computed
separately for long and short legs, independent minimums (§59.4).

**5.3 Derive all thresholds** — `N_eff,long`, `N_eff,short`, `g_min`,
`C_signal,min` — from the intended diversification and risk architecture, per
§59.4.4's may/may-not list. One paragraph of justification each. `N_eff ≥ 6`
and `g_min = 0.70` are starting concepts only.

## 6. The complete state-transition table

§59.11.1 requires transitions for gate failures. **Extend to every non-formed
category** — `D_structural` and `D_operational` days can also occur while
positions are held, and must not inherit behaviour accidentally.

Specify the total function:

```
T(calendar_state, failed_conditions, w_{t-1}) → w_t
```

covering `D_gate`, `D_structural`, `D_operational`. Requirements:

- **a single common transition is preferred** (§59.11.1.2); per-category
  deviation only where a named risk/economic invariant demands it
- **concurrent failures deterministic** (§59.11.1.3)
- **any "hold" variant states its leverage-drift consequence** (§59.11.1.4)
- consider explicitly: a solver failure with a live book is an *operational*
  event, and "hold indefinitely on repeated solver failure" is how Gen-1's
  runaway leverage happened

**6.1 Causal precedence** (§59.11.2.1): define the classification order
reflecting the stage at which the intended decision became impossible —
`structural eligibility → optimizer → feasibility gates → execution` or a
justified alternative — so categories are invariant to control flow.

## 7. Optimizer specification

Objective, constraints, and the §59.5 determinism items: solver and version,
seeding/threading, weight tolerance, primal feasibility tolerance, maximum
factor-constraint residual, maximum dollar-neutrality residual, accepted
termination states, deterministic handling of near-equivalent optima. Include
the turnover/transaction-cost term and the volatility target as **frozen**
inputs (§59.3.2).

## 8. Kill criteria — exact quantities (§59.7)

Derive, from RCM's own architecture rather than Gen-1's outcome:

- the **minimum formation rate** — from holding-period and turnover logic:
  what activity level does a daily-rebalanced cross-sectional strategy require
  to be that strategy?
- the exact **residual-momentum statistic** and its criterion
- the **forward feasibility rolling gate**

## 9. Deliverable

`NOTES` §60 containing §1–§8, each quantity with its derivation, plus a
**parameter manifest**: every frozen constant, its value, and the one-line
argument that fixed it. Anything that cannot be derived without performance
data must be listed explicitly as **UNRESOLVED**, not guessed — an honest gap
is a finding; a fabricated justification is not.

## Acceptance

- §60 appended; §59 and earlier unedited
- §0's estimation-vs-selection distinction recorded
- ETH orthogonalized; beta estimator and `V_i` frozen
- `μ_mom` in return units with frozen PIT calibration, shrinkage rule, and the
  §2.3 carry-degeneration guard incl. threshold and consequence
- Funding sign convention explicit
- Reliability-multiplier rejected with reason; chance constraint specified;
  `z` and `ε_β` derived with arithmetic shown
- Timeline frozen with identical holding intervals for `r_shadow` and
  `r_actual_price`
- Bounded `C_signal` recorded as a §59.4 correction; all four thresholds
  derived with justification
- Total transition function over all non-formed categories; causal precedence
  defined
- Optimizer and determinism items frozen; kill criteria quantified
- Parameter manifest; UNRESOLVED items listed honestly
- **No code, no market/return/performance data. Gen-1 15 of 25; Gen-2 0 of 20.
  Holdout sealed.**

## Do not

- Choose any value by comparing performance, formation rates, or "what would
  have traded more"
- Confuse estimation with selection in either direction
- Leave any non-formed calendar state without a transition rule
- Use the reliability-multiplier form
- Let `r_shadow` and `r_actual_price` differ in holding interval
- Guess a threshold rather than marking it UNRESOLVED
- Write optimizer code, access data, or touch the holdout
