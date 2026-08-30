# Stage 18 (revised) — RCM Generation-2 governance pre-registration

**No code. No optimizer. No market data, return data, production snapshots,
backtest results, or performance diagnostics.** Repository text and the ledger
may be read. This stage writes one ledger section: the governance under which
Strategy Generation 2 (Residual Carry Momentum) may be developed.

§0 of `STAGE2_PROMPT.md` remains in force. Gen-1 frozen and complete. Holdout
sealed.

**Append as the next unused top-level NOTES section — expected §59** (§58 is
occupied by the RCM proposal review).

---

## 0. Why governance before mathematics

RCM was conceived **after** observing the 2026 structural failure. Information
from the 2025–2026 regime already shaped the hypothesis class — reliability
weighting, continuous construction, avoidance of fixed-rank legs. Policy
written after the specification would be written by someone who already knows
what the market did.

---

## 1. Data policy

**1.1 Development: 2020–2024.** Implementation, structural diagnostics, signal
decomposition, robustness work, parameter pre-registration.

**1.2 2025-01 → 2026-07 SEALED.** *Not* a Gen-2 validation period. Grounds,
recorded explicitly: RCM's hypothesis class was influenced by that regime's
observed structure, so treating it as independent would overstate its
independence. The seal is **re-affirmed on Gen-2-specific grounds**, not merely
inherited.

**1.3 Primary validation: forward-only paper trading**, on data postdating the
frozen RCM v1 specification.

**1.4 Sealed window's status:** an optional *final historical challenge set*,
openable only after RCM v1 is frozen AND credible forward evidence exists. One
look, ever, by deliberate user decision.

**1.5 The seal is enforced structurally, not textually.** Stage 19 must
specify, and implementation must provide: the Gen-2 research runner **hard
rejects** any request whose timestamp range intersects the sealed interval.
Opening the challenge set requires an explicit unlock flag **plus a ledger
entry written before execution**. This is the `PITView` principle — a
guarantee, not a promise.

**1.6 The forward boundary is a commit and a timestamp.** Record the exact
RCM-v1 freeze commit hash and UTC timestamp. Forward validation counts strictly
data after it. **No retroactive backfill** into the forward period.

## 2. What forward validation can and cannot establish

**CAN:** feasibility (does a recognizable book form, with breadth, at intended
risk), machine correctness, execution and cost realism, operational integrity —
precisely where Gen-1 failed.

**CANNOT: confirm alpha.** Months of forward paper has negligible statistical
power — the Gen-1 MDE wall (§28.4, §45.9), indifferent to data freshness. A
clean forward record is **not** evidence the edge works and must never be cited
as grounds for increasing capital.

## 3. Trial budget — a ceiling, not an allowance

**20 Gen-2 trials maximum**, independent of Gen-1's. Intent: RCM v1 should
consume **very few**.

**No trial** (no performance comparison): PIT alignment, solver feasibility,
beta-estimator simulation, optimizer constraint tests, exchange-filter
validation, reconciliation tolerance calibration, sizing/quantization tests,
return-blind universe measurement, null/synthetic harness tests.

**Consumes a trial:** any real-data run whose performance result could cause a
preference between alpha or portfolio specifications.

### 3.1 Indeterminate is a valid outcome

> If an allowed comparison produces a difference too small to resolve at the
> pre-registered statistical precision, the result is **INDETERMINATE** — not a
> win for the larger point estimate. The simpler / pre-registered baseline
> stands.

**Operationalization, mandatory:** the resolvable difference (MDE) and the test
are stated **before** the comparison runs, so "too small to resolve" cannot be
argued after seeing numbers. This generalizes Gen-1's §45 buffer ruling into a
standing rule.

### 3.2 Frozen analytically — never swept

Fixed by reasoning, no performance comparison, ever:

**Signal & construction:** momentum windows 2–21 and 22–63 (non-overlapping);
0.6/0.4 weighting; residual construction; BTC+ETH factors; cross-sectional
normalization and winsorization rule.
**Estimation:** beta estimation method and window; covariance estimator and
window; expected-funding estimator and forecast horizon; liquidity metric and
window.
**Portfolio:** reliability functional form; conviction mapping; optimizer
constraints; solver tie-breaking rule; volatility target; daily rebalance;
transaction-cost treatment; 180-day minimum history.

The estimation block is listed explicitly because freezing visible knobs while
leaving estimator choices open would defeat the purpose.

### 3.3 Funding enters at economic value — λ eliminated by default

Default requirement: express expected momentum and expected funding in
**comparable return units** so funding enters at its economic value:

```
μ_total = μ_momentum − F        (i.e. λ = 1, not a free parameter)
```

A λ comparison is permitted **only if** Stage 19 demonstrates the momentum
signal cannot be put into comparable units — and then it is small,
pre-registered, trial-logged, and subject to §3.1.

## 4. Feasibility gates — three vacuities, plus leg symmetry

Degradation is permitted **only while the portfolio remains recognizably the
same strategy.**

**4.1 Concentration.** `N_eff = (Σ|w_i|)² / Σw_i²`, **computed per leg**:
`N_eff,long` and `N_eff,short` with **independent minimums**. Total breadth can
hide asymmetric collapse — a nominally neutral book with one meaningful short
is not the hypothesis.

**4.2 Exposure.** `G_realized / G_target ≥ g_min`.

**4.3 Signal coverage.** `Σ|w_i||S_i|` retained after feasibility, relative to
the same quantity on the canonical pre-feasibility book. **Denominator defined
canonically:** the signed continuous target after alpha, risk and factor
construction but **before** exchange/min-notional/quantization feasibility.
Guards against a book keeping breadth and gross while discarding the strongest
signals because they are hardest to trade.

**4.4 Threshold derivation — what Stage 19 may and may not use.**
`N_eff ≥ 6` and `G_realized/G_target ≥ 0.70` are **starting concepts, not
adopted numbers.**

*May use:* algebra, the intended diversification and risk architecture,
exchange rules, synthetic fixtures, return-blind metadata.
*May NOT use:* historical PnL, Sharpe, realized alpha, historical formation
rates, or "which threshold would have traded more days."

A day failing any gate is a **skip**, recorded with the failing gate.

**4.5 Gate-skips are accidentally protective — the Gen-1 lesson.** §7 measured
this: the $400 book's flattering 14.78% drawdown existed because skipped days
sat out losses; healing the skips moved it to 24.79%. Therefore **all
development-era performance is computed on the full calendar, not on formed
days only**, and **formation rate is reported beside every performance
number.** Otherwise the gates protecting against vacuity quietly flatter
results.

## 5. Numerical reconciliation and optimizer determinism

Bit-exactness is not assumed. Stage 19 freezes: solver and version;
deterministic seed/threading; weight tolerance; **primal feasibility
tolerance; maximum factor-constraint residual; maximum dollar-neutrality
residual; accepted solver termination states; deterministic handling of
multiple or near-equivalent optima.**

Weights within shadow tolerance are insufficient if they subtly violate the
economic constraints. A mismatch beyond tolerance remains stop-and-diagnose.

## 6. Inheritance from Generation 1

Reused unchanged: PIT store and lookahead discipline, crypto universe filter
and composition guard, read-only production data path, fill simulator,
pre-registered execution model, shared quantized sizing, settle/reconciliation
primitives, risk layer, kill switch, watchdog, supervisor, dashboard and
alerts, cost log and venue tags, trial-budget and pre-registration conventions.

Inherited **must-re-verify** list for any real-money venue: the
`reduce_only_exempt` floor rule (§57.2) and layer-1 stop availability (§56.9) —
both testnet-measured only.

## 7. Kill criteria — exact, deferred to Stage 19 quantities

RCM is abandoned, not patched, if:

- formation rate falls below the **Stage-19 pre-registered minimum**;
- the **Stage-19 pre-registered residual-momentum statistic** fails its exact
  criterion on development data;
- forward feasibility breaches its **pre-registered rolling gate**.

**Gen-1's ~78% is deliberately not inherited.** RCM must derive its own minimum
viable activity level from what it claims to be — a daily-rebalanced
cross-sectional strategy — via holding-period and turnover logic, not from the
outcome of the strategy it replaces.

## 8. Post-freeze change control

After the RCM-v1 freeze:

- Any strategy-layer change altering target weights, eligibility, signals, risk
  allocation, or execution intent creates **RCM v2** and **restarts the
  forward-validation clock**.
- A PnL-affecting bug fix **voids the affected forward segment** rather than
  continuing under the same evidence record.
- Purely operational fixes that **provably do not alter intended targets** may
  remain v1; the proof is recorded.

This prevents forward validation from becoming another development dataset.

## 9. Research order — fixed

```
governance (this stage)
  → mathematical specification + threshold derivation (Stage 19)
  → synthetic/structural implementation and null tests
  → 2020–2024 development
  → FREEZE RCM v1 (commit + UTC timestamp recorded)
  → forward paper validation
  → (much later, optional) the sealed challenge set
```

No optimizer, signal, or portfolio code before Stage 19 exists in the ledger.

## Acceptance

- Appended as the next unused top-level section (expected §59), dated,
  containing §1–§8 verbatim in substance
- Seal re-affirmed on Gen-2 grounds; structural enforcement (§1.5) and the
  freeze-commit boundary (§1.6) specified as Stage-19 requirements
- "Forward validation cannot confirm alpha" recorded
- Trial ceiling, the §3.2 frozen list including estimator choices, the §3.1
  INDETERMINATE rule with mandatory pre-stated MDE, and §3.3 λ-elimination
  recorded
- Three gates defined; per-leg `N_eff`; canonical coverage denominator;
  thresholds deferred to Stage 19 with the §4.4 may/may-not list; §4.5
  full-calendar rule recorded
- Optimizer determinism items and kill criteria recorded; 78% not inherited
- Post-freeze v1/v2 change control recorded
- **No code. No market/return/performance data accessed. Gen-1 budget 15 of 25
  unchanged; Gen-2 0 of 20. Holdout sealed.**

## Do not

- Write optimizer, signal, or portfolio code
- Choose any threshold numerically beyond naming starting concepts
- Access market data, returns, snapshots, backtest results, or performance
  diagnostics
- Treat 2025–2026 as a Gen-2 validation period
- Frame forward paper results as alpha evidence
- Select between statistically indistinguishable results
- Split or edit the ledger
