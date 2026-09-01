# Stage G3-0 (v2) — Generation-3 governance and the first killable phase

**No code beyond the data audit. No forecast fitted. No return-based result.
No trial consumed.** This stage writes Gen-3's governance as `NOTES` §68,
inheriting Gen-1/2's method, dropping the invariants both generations died
on, and pre-registering the first killable test in outline.

§0 of `STAGE2_PROMPT.md` remains in force. Gen-1 frozen (15 of 25). Gen-2
abandoned (1 of 20). Holdout sealed.

---

## 1. Design signature (§68.0)

> **Gen-3 replaces arbitrary gates with measured uncertainty.** Breadth,
> maturity, holding period, forecast confidence, allocation, and model trust
> are all continuous quantities estimated from data, not categorical rules
> chosen by a designer. Where a hard rule remains, it is a *safety* rule or a
> *data-integrity* rule, never a modelling convenience.

This is the structural difference from Gen-1 and Gen-2, both of which died on
fixed invariants that were free at institutional scale and fatal at $800.

## 2. Thesis and construction principle (§68.1)

**2.1 Thesis:** short-horizon crypto returns increasingly reflect
time-varying transmission of macro, cross-asset, and information shocks; a
useful system models the state and propagation of information across markets
and lets predictor relevance evolve.

**2.2 Construction principle — USER DECISION, verbatim:**

> **Breadth follows evidence.** No required number of names exists anywhere in
> Gen-3 — no `N_eff` floor, no `MIN_LEG_NAMES`, no forced substitution, no
> position added for diversification alone. `N_t = #{i : w_i,t ≠ 0}` is an
> **output**. Nothing qualifying ⇒ **flat, a correct output**. One name
> qualifying ⇒ that name and cash. Twelve ⇒ a risk-sized twelve-name book,
> floor-aware by construction. Effective breadth is **reported**, never
> required.

**2.3 Why (§68.1.3):** every non-formed day across three generations —
Gen-1's 21% train skips, the 0-of-12 live in 2026, Gen-2's 98.8%
non-formation with median surviving breadth 4.16 against a required 6 — was a
breadth requirement colliding with $800 against $5 floors. **Not one was a
signal failure.**

**2.4 No artificial cash reserve.** The system protects **risk capacity and
bank survival**, not a fixed idle-cash percentage. Unused capital is not a
failure. Cash competes with every risky asset in the allocator.

**2.5 Derived, and stated, sizing quantities:**
- **Conviction threshold, derived:** act only where expected return beats the
  frozen cost model — `|P(up) − 0.5| > c_rt / (2·E|r|)`; ≈ 0.023 at 13 bps
  and 2.8% BTC mean absolute daily move. Updates mechanically; never by hand.
- **Risk penalty, not a vol target:** `max_w μ̂ᵀw − λ wᵀΣw − C(w − w_prev)`
  subject to hard caps. **`λ` is the owner's risk aversion**, a preference
  stated before data — the fixed-vol-target parameterization is dropped
  (§8 of the refinements), not the preference behind it. The earlier "30–40%
  BTC" is recorded as a confidence-conditioned allocation preference, to be
  fixed as `λ` plus caps in the spec stage.
- `C(·)` is the **frozen cost model**, not a free coefficient.

## 3. Adopted invariants from the refinements (§68.2)

**3.1 Probability is not capital.** Allocation depends on net-of-cost
expected return, calibrated probability, forecast uncertainty, tail risk,
liquidity, cost, correlation with the existing book, model-health confidence,
and hard caps — never on `P(up)` alone.

**3.2 Calibration precedes sizing.** Raw model probabilities are calibrated
point-in-time before any allocation uses them; a conservative/lower
reliability bound may be used for sizing. Method fixed in the spec stage.

**3.3 Net-of-cost edge decides.** `μ_net = E[r|F_t] − C_i ≤ 0 ⇒ position
zero`, normally.

**3.4 Losses never increase exposure.** If a trade's PnL is negative,
`|w_target| ≤ |w_current|` for that asset. **Precedence, fixed now:** where
§3.4 conflicts with the continuous re-justification of §3.5 (forecast
strengthens after a loss), **§3.4 wins.** No averaging down, no recovery
trading. This is an owner preference, not a derivation.

**3.5 Every position continuously re-earns its place.** No mandatory holding
period. The standing question is: *if flat right now, would current
information justify opening this position?* Yes ⇒ hold/resize; weaker ⇒
reduce; no ⇒ exit; reversed ⇒ exit first, then evaluate the opposite as a new
trade.

**3.6 Two exit classes, both forecast-driven.** (i) *Invalidation*: the
realized path entered a region the entry forecast called unlikely
(`r_path < Q_α(F̂_entry)`); (ii) *Updated forecast*: remaining expected edge
no longer covers cost and required risk compensation. A separate catastrophic
stop exists. `α` fixed in the spec stage.

**3.7 The invalidation rate is the model-health statistic.** A calibrated
forecast is invalidated at rate `α` by construction; realized invalidation
materially above `α` is direct evidence of miscalibration and feeds §3.8 —
no separate estimator needed.

**3.8 Forecast confidence and model-health confidence are separate.**
`C_forecast,i` and `C_model-health` are maintained apart; poor recent
calibration raises effective uncertainty (`σ_eff = κ_t σ_model`, `κ_t ≥ 1`)
and lowers permitted size **without flipping forecast direction**. Recovery
follows a rule frozen before live use.

**3.9 Losing money is not automatically a bug.** The trigger for
investigation is *incompatibility with the system's own forecast
uncertainty, risk model, or operational invariants* — not negative PnL
(§60.12.5's bug-vs-design rule, generalized).

## 4. Hierarchical cold-start — the juvenile architecture (§68.3)

**4.1 The problem it solves.** Gen-1 died on a 2026 universe of
unhedgeable young listings; Gen-2's signal reached for exactly those names
while its hedge guard refused them. Age-threshold exclusion throws away real
opportunity; naive inclusion imports unquantifiable risk.

**4.2 The model.** For each asset:

```
r_i = β_M,i·M + β_chain,i·F_chain + β_sector,i·F_sector + α_i + ε_i
```

**4.3 Per-coefficient posterior shrinkage — no maturity curve, no
threshold.** Each coefficient shrinks toward its group prior by its own
posterior precision ratio:

```
E[β_i | D_i] = λ_i·β̂_i + (1 − λ_i)·μ_group ,     λ_i = τ²_group / (τ²_group + s²_i)
```

with **separate** `λ_i,M`, `λ_i,chain`, `λ_i,sector`, `λ_i,α` — a token's
chain beta may become clear in a week while its own alpha stays unknowable
for a year. `s²_i` is the token's estimation variance; `τ²_group` is
estimated across the group (empirical Bayes). **No age curve, no 30/60/90-day
rule, no promotion decision, no owner discretion.** `s²_i → ∞` ⇒ `λ → 0` ⇒
pure inheritance; evidence accumulates ⇒ `λ → 1`.

`juvenile` / `mature` may appear as **dashboard labels with zero mechanical
meaning.**

**4.4 Posterior uncertainty propagates.** Every inherited parameter carries
its posterior uncertainty into the covariance, the chance constraints, and
sizing. **Recorded precisely:** hierarchical shrinkage does not make young
assets hedgeable — it **converts hedgeability from a binary certification
problem into a continuous estimation-and-uncertainty problem.** A
`β = 1.4 ± 0.9` is a valid estimate and a poor hedge; the risk engine sizes
accordingly instead of the guard refusing the day.

**4.5 PIT ancestry, frozen and boring.**
- Chain from the token's native/canonical chain; sector from a **frozen
  metadata taxonomy**; both assigned by a frozen rule from information
  available **at that time**.
- **No LLM taxonomy decisions** — the §66.0.1 fabrication risk applied to the
  input that decides which prior a coin inherits.
- **Taxonomy changes are themselves PIT:** a token publicly classified "AI" in
  2026 may not carry an AI-sector prior in its 2022 model.
- **Ambiguity → fewer parents.** Multi-chain or category-ambiguous ⇒ no chain
  and/or no sector prior; the hierarchy fails **toward the broadest defensible
  prior** (market/BTC alone).
- The tree's *levels* are frozen before any test; the grouping may not be
  discovered by which one predicts best.

**4.6 Leave-one-out, PIT group priors.** When forecasting asset `i`, the
group prior `μ_group,t` is computed **excluding `i`'s own contribution** and
using only information available at `t`. Without this, the prior partially
predicts what it is meant to predict — a self-leak that would look like the
hierarchy working.

**4.7 `UNMODELABLE` — the sole hard maturity-related state.** A
data-integrity floor, not a breadth floor: absent reliable price, volume,
depth, token identity, venue access, or basic return history ⇒ **no forecast,
zero allocation.** The machine cannot quantify what it does not observe.

**4.8 The emergent sizing property — no juvenile penalty needed.** A juvenile
whose forecast is largely inherited is economically *a worse way to buy its
parent*: same directional bet, more idiosyncratic variance, worse execution.
A correlation-aware allocator carrying the factor structure therefore routes
exposure to the parent and leaves the juvenile small **unless its own
posterior alpha is strong**. No `juvenile_haircut`, no `young_token_max_weight`
is introduced unless later survival analysis establishes an independent need.

## 5. Inheritance from Gen-1/2 (§68.4)

Reused unchanged: PIT store and lookahead discipline; crypto universe filter
and composition guard; read-only production data client; fill simulator and
frozen execution model; shared quantized sizing; settle/reconciliation
primitives; risk layer, kill switch, watchdog, supervisor, dashboard, alerts;
cost log; the §60.12 IC evaluator and Gen-1 bootstrap code; the
§59.11.2/§62.4 calendar classification; the §63.6 factor-structured residual
covariance and its MP-edge rank rule; pre-registration, trial-budget,
INDETERMINATE, void, and append-only-ledger conventions. Must-re-verify list
(§57.2, §56.9) inherited.

**Not inherited:** any breadth requirement; market-neutrality as a
requirement (BTC is a traded direction, not a hedged factor); the RCM
residual-momentum signal; the fixed volatility target.

## 6. Data policy (§68.5)

- **Development 2020-01-01 → 2024-12-31**, sequential-in-time only.
- **2025-01 → 2026-07 SEALED**, re-affirmed on Gen-3 grounds: the thesis was
  formed observing that window. Runner rejection unchanged.
- **Validation forward-only** from a recorded freeze commit + UTC timestamp;
  forward validation establishes feasibility and machine correctness and
  **cannot confirm alpha**.
- Every external source carries a **first-public timestamp**; event date and
  availability date never conflated.

## 7. Standing rules (§68.6)

1. **Horizon is decided by arithmetic, not doctrine.** A horizon is
   admissible for *trading* only where development testing shows forecast
   edge exceeding **turnover-adjusted** cost at that horizon. Realised
   turnover is a pre-registered disclosure beside every skill number.
   (Reference: at 13 bps round-trip, daily repositioning ≈ 47%/yr drag,
   weekly ≈ 7%/yr.) Sub-daily *measurement* is always permitted.
2. **`M0` includes carry.** Funding carry is the crypto-native daily return
   that demonstrably exists (60% of Gen-1 PnL; Gen-2's carry guard fired on
   all 1,642 days). Exogenous tests are incremental beyond state **and** carry.
3. **Two numbers beside every skill test:** the statistical criterion, and a
   pre-registered **disclosure** of economic relevance (edge vs
   turnover-adjusted cost; expected trade count). Disclosure, not gate.
4. **Feasibility at $800 by construction** — guaranteed by §2.2 and tested.
5. **The neural network is a ladder rung, not the spine.** Admissible only as
   `M2`+ against the simple model under the same incremental criterion; all
   architecture and hyperparameters selected **inside** training windows by a
   frozen nested procedure, never against out-of-sample-in-time results;
   INDETERMINATE leaves the simpler model standing.
6. **The LLM event layer is quarantined to a later rung.** When it comes:
   closed-taxonomy classification and surface facts only; model version and
   prompt hash frozen as parameters; every extraction source-hashed;
   predictive tests **with and without** judgment fields; and the recorded
   fact that prior-leakage is partly unmeasurable.
7. **Synthetic fixtures match real dimensions.** No lock without a dry run at
   development-scale `N` (F-2/F-3 were "fine at 25, broken at 200").
8. **Hard gates on the ladder.** No macro-event, corporate, congressional, or
   news infrastructure is built until `M1` passes its pre-registered test.
9. **No hand-labelled regimes.** Continuous state variables only.
10. **Operational architecture is required but deferred** (§8).

## 8. Operational architecture — adopted as requirement, built after the test (§68.7)

The refinements' §18–38 are **adopted now as design requirements**, so no
eventual live system may be built without them, and **built only after
forecast skill is established** — the §25 ordering applied to itself, because
Gen-2's real cost was engineering around a signal that did not exist.

Recorded as required: the four safety layers and Level 0–5 authority
hierarchy (authority moves upward only); position / portfolio / infrastructure
supervisors with asset, component, and global circuit-breaker scopes;
**recovery vs repair** — *the machine may restore known-good operation; it may
not redefine what "good" means*; the explicit bounded autonomous-recovery
list with `max_attempts` / `max_recovery_time` / `max_recurrence_count`;
persistent `HUMAN_INTERVENTION_REQUIRED` latches surviving restart and
reboot, clearable only by the owner; the `H_*` fault families including
**`H_UNKNOWN` failing closed** (absence of diagnosis is never safety); the
forbidden-autonomous-repair list; forensic incident snapshots; owner-controlled
resume with resolution classes; and post-incident probation with
pre-specified conservative constraints.

**Central rule, recorded verbatim:** *If our understanding of reality becomes
questionable, stop trading first and investigate second.*

## 9. Trial budget and sequential protocol (§68.8)

- **Ceiling 20 trials.** A trial = any real-data result that could cause
  preference between forecast or portfolio specifications. Structure
  measurements are not trials.
- **INDETERMINATE is valid**: the simpler / pre-registered model stands.
- **Sequential-in-time development:** expanding-window fits refit at calendar-
  year boundaries; forecasts for 2021–2024 each produced by a model that saw
  only prior years (~1,460 out-of-sample-in-time daily forecasts). At no point
  does 2024 inform what the model believed in 2021.

## 10. Phase one — the first killable test (§68.9)

**G3-A — Data audit and PIT policy (no trial).** For ES, NQ, VIX, US 2Y/10Y,
DXY, gold, BTC, ETH: availability to 2020, granularity, cost, first-public
timestamp semantics, and the exact observation usable at each decision time.
Report what must be procured. **Stop if a required source is unavailable at
any price**; report substitutes without adopting them.

**G3-B — Cross-asset structure (no trial).** Rolling lead/lag
`Corr(r_X,t, r_BTC,t+k)` and rolling betas at 1h/4h/daily where data permits —
**measurement, raw series, no narrative labels, no thresholds** (§63.2
protocol discipline). Window, `k` range, and statistics frozen before reading.

**G3-C — The first pre-registered test (ONE trial).** Two models of frozen
form and frozen feature lists (≤ 8 features each, fixed in the spec stage
from architecture, no sweeps):

```
M0 : crypto-native state + carry
M1 : M0 + cross-asset state (PIT-aligned prior-period ES, NQ, VIX, 2Y, 10Y, DXY, gold)
```

Scored on **every** out-of-sample-in-time day at the daily horizon **and** one
pre-registered sub-daily horizon (measurement power; trading admissibility
still governed by §7.1):

| Q | Test | Criterion |
|---|---|---|
| Q1 | `M0` BTC-direction skill vs climatology | Brier skill score, stationary-bootstrap 90% CI (inherited code), `CI_lower > 0` |
| Q2 | `M1` incremental over `M0`, BTC direction | paired Brier difference, `CI_lower > 0`; INDETERMINATE ⇒ `M0` stands |
| Q3 | `M0` cross-sectional skill (mature names) | §60.12 daily-IC evaluator, `CI_lower > 0` |
| Q4 | `M1` incremental cross-sectional skill | paired daily-IC difference, `CI_lower > 0`; INDETERMINATE ⇒ `M0` stands |

Beside each: calibration/reliability report; realized CI half-width as
resolving precision (§60.12.3 disclosure — no fabricated MDE); the §7.3
economic disclosure.

**Consequences, pre-registered:**
- Q2 or Q4 PASS ⇒ exogenous thesis supported at that level; the next rung
  earns its turn (§7.8).
- Q2 and Q4 fail/INDETERMINATE but Q1 or Q3 PASS ⇒ exogenous thesis **not
  supported**; the §2.2 construction may proceed on crypto-native forecasts,
  **relabelled as such**. No discretion.
- All four fail ⇒ **Gen-3 stops before any book exists**, having cost a data
  audit and one measurement.

**The hierarchical cold-start layer (§4) is a later rung**, tested only after
Q3/Q4 establish cross-sectional skill on mature names. Its pre-registered
trial: replay the first 90 days of **now-mature** tokens, comparing
`M_own` vs `M_BTC-prior` vs `M_hierarchical`, strictly PIT (day-3 forecasts
use only through-day-3 information; later maturity supplies outcomes only)
and with §4.6 leave-one-out priors. In phase one, juveniles sit below the
§4.7 integrity floor.

## 11. Kill criteria form (§68.10)

Exact quantities derived in the spec stage; the **form** fixed now: (i) the
Q-table for phase one; (ii) for any eventual construction, a minimum activity
level derived from **what a breadth-follows-evidence strategy claims** —
expected conviction-day frequency from the forecast's own calibration, never
inherited from Gen-1/2; (iii) a forward feasibility gate. **Abandon, not
patch.**

## Order of work

1. §68.0–§68.10 appended, dated; user decisions labelled as such
2. G3-A audit; report procurement needs; stop if blocked
3. G3-B protocol frozen in the ledger **before** reading; measurement; raw
   series appended
4. **Stop.** The spec stage (feature lists, forecast form, `λ` and caps,
   calibration method, `α`, exact criteria, lock commit) follows, reviewed by
   both delegates before the single trial runs.

## Acceptance

- §68 appended with: the §1 design signature; §2.2 verbatim as user decision;
  §3 adopted invariants incl. the §3.4 precedence rule; §4 hierarchical
  cold-start with per-coefficient shrinkage, PIT taxonomy, leave-one-out
  priors, uncertainty propagation, `UNMODELABLE` as sole hard state, and the
  §4.4 phrasing exactly; inheritance and non-inheritance lists; data policy
  with the seal re-affirmed on Gen-3 grounds; the ten standing rules; §8
  operational requirements adopted-but-deferred; budget and sequential
  protocol; the Q-table with pre-registered consequences; kill-criteria form
- G3-A report; G3-B protocol frozen before reading and its series appended
- **No forecast fitted, no return-based result, Gen-3 0 of 20, holdout sealed**

## Do not

- Introduce any breadth floor, maturity threshold, promotion rule, or
  juvenile allocation penalty
- Let an LLM assign ancestry, or apply a present-day taxonomy to a past date
- Compute a group prior including the asset it forecasts
- Fit `M0`/`M1`, read development returns for any forecast, or run G3-C
- Build the neural network, the event/LLM layer, or the operational
  supervisory system in this phase
- Attach a narrative label to any G3-B series
- Touch the holdout
