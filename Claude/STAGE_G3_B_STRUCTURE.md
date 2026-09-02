# Stage G3-B — Cross-asset structure: freeze the protocol, then measure

Three parts. **Part 0** lands the provenance-quality refinement both delegates
agreed on. **Part I** freezes the measurement protocol **and M1's lag
structure** in the ledger *before any series is read* — the second closes a
contamination path in §68.9. **Part II** runs the measurement and emits raw
series with no narrative labels.

**No forecast fitted. No model compared. No trial consumed. Gen-3 0 of 20.
Holdout sealed** (runner rejection re-exercised before the first read).

Appended as `NOTES` §69. §0, §63.2 protocol discipline, and §68 as amended
(§68.11, §68.12) govern.

---

# PART 0 — Provenance quality (§69.0)

The FRED-sourced Fed series use an **assumed** conservative availability, not
an observed publication timestamp. Record that distinction:

```
source_available_time         = <as resolved in §68.12.1>
source_availability_quality   = "conservative_assumption" | "observed"
source_availability_basis     = "publisher + 1 business day, same local time"
```

becoming `"observed"` only if genuine historical aggregator timestamps are
obtained or a direct publisher feed replaces the aggregator.

**Why this matters, recorded:** the conservative rule discards real
information. Without the quality field, a failed cross-asset result cannot be
distinguished between *"the information genuinely does not predict"* and
*"we handicapped the data by ~24h because exact historical aggregator timing
was unavailable."* Those are different findings.

**Two invariants, pinned by tests:**

```
source_available_time ≥ underlying_public_time        (per observation, always)
PIT reader: source_available_time ≤ decision_time     else the observation is NOT returned
```

# PART I — The protocol, frozen before reading (§69.1)

## I.1 CONTAMINATION CLOSURE — M1's lag structure is frozen first

G3-B measures which lags of each exogenous series lead BTC. The spec stage
then selects M1's features. **If the map informs which lag enters M1, that is
selection on development returns performed without spending a trial** — the
trial-budget mechanism bypassed by a "measurement." Same shape as choosing a
vol target from a contaminated drawdown.

**Frozen now, from architecture, before any series is read:**

> **M1 uses, for each adopted exogenous series, the single most recent
> PIT-available observation at the decision time — one lag, no lag search.**
> This is what the §68.11.1.2 access rule already implies. No lag, window, or
> transform in M1 may be chosen from the G3-B map.

The map is therefore **descriptive only**: it characterises transmission and
its variation over time. It is **quarantined from feature selection**, and
that quarantine is recorded here, not asserted later.

## I.2 Scope — Panel A only

Panel A (daily) alone. Panel B requires licensed futures data and an owner
decision on whether the intraday branch is activated; **G3-B does not wait on
it and does not substitute cash indices for futures** in any intraday claim
(§68.11.2).

Series: BTC, ETH (PIT store) and the adopted-pending Panel A exogenous set,
read through the PIT reader under Part 0's invariants. Development window
**2020-01-01 → 2024-12-31** only.

## I.3 Statistics — every object defined before reading

**Window:** the frozen **90-day** estimation window (§63.6 precedent). No
other window.

**Alignment:** one observation per **UTC calendar date** in the development
window. TradFi series are **stale-carried** with their own knowable-at stamps;
the number of carried (stale) days is itself reported per date. Staleness is a
state to report, never to hide.

**Lead/lag:** for each exogenous series `X` and each `k ∈ {−5, −4, −3, −2, −1,
0, +1, +2, +3, +4, +5}` calendar days, the rolling Pearson and Spearman
correlations

```
ρ_k,t = Corr( r_X,(t−89 … t) , r_BTC,(t−89+k … t+k) )
```

computed only where both legs are fully PIT-available at `t`; `k > 0` terms
that would require post-`t` BTC returns are **not computed at `t`** — they
appear only at the later date when they become available. A test asserts no
`ρ_k,t` uses any input with `available_time > t`.

**Rolling betas:** `β_X,t` from the same 90-day window, with standard errors.

**Reported per date, as raw series, for each `X` and `k`:** `ρ_k,t` (both
correlation types), `β_X,t`, `SE(β_X,t)`, the sample size actually used, and
the stale-day count. Plus, across dates, the distribution (p5/p25/p50/p75/p95)
of each.

**No thresholds. No narrative labels. No "regime" statistic.** The delegates
read the series; they do not receive a story (§63.2 discipline).

## I.4 The delay-cost sensitivity — descriptive, quarantined

To quantify what Part 0's conservative assumption costs, the same map is
computed a second time under **publisher-release timing** instead of the
aggregator's assumed availability, and reported side by side as a clearly
labelled **sensitivity**.

**This does not adopt publisher timing.** Its only purpose is to make the
§Part-0 distinction measurable — how much transmission the conservative rule
discards. Like the map itself, it is **quarantined from feature selection and
from any adoption decision**, and it is labelled
`SENSITIVITY — NOT THE PIT-VALID SERIES` wherever it appears.

## I.5 What G3-B is not

- Not a trial: no forecast is fitted, no specification is compared, no result
  can cause preference between models (I.1 guarantees this by freezing the lag
  structure first).
- Not an adoption decision: the Panel A substitutes remain `adopted: false`
  (§68.11.2); measuring with a series does not adopt it, and this is stated in
  the output header.
- Not evidence of predictability: a correlation map is not a forecast test.
  Q1–Q4 remain the only skill criteria.

# PART II — Execution (§69.2)

1. Re-exercise the sealed-interval rejection (a request one day into 2025 is
   refused) **before** the first read.
2. Compute I.3; append raw series and distributional summaries to §69.2 and
   `diagnostics.jsonl`.
3. Compute I.4; append labelled as sensitivity.
4. Report: the series, the stale-day profile, the sample sizes, and **nothing
   else** — no interpretation, no recommendation, no "X leads BTC by N days."

## Order of work

1. §69.0 and §69.1 appended, dated — **including the I.1 lag freeze** —
   before any series is read; Part 0 tests green
2. §69.2 measurement; raw series appended
3. Report. **Stop.** The spec stage follows (feature families and their frozen
   transforms, forecast form, `λ` and caps, calibration method, path-statistic
   definition for invalidation, exact Q1–Q4 criteria, lock commit), reviewed by
   both delegates before the single G3-C trial.

## Acceptance

- Provenance quality fields present; both invariants pinned by tests
- §69.1 appended **before** any read, containing the I.1 lag freeze and its
  quarantine verbatim
- Sealed-interval refusal re-exercised and logged
- Lead/lag and beta series emitted per I.3 with sample sizes and stale-day
  counts; no threshold, label, or narrative anywhere
- Delay-cost sensitivity emitted and labelled `SENSITIVITY — NOT THE
  PIT-VALID SERIES`
- Output header states: no adoption, no trial, no skill claim
- **Gen-3 0 of 20; holdout sealed; no forecast fitted**

## Do not

- Read any series before §69.1 is appended
- Let the map, or the sensitivity, influence any feature, lag, window, or
  transform in M1
- Adopt any Panel A substitute
- Make any intraday claim from cash-index data
- Attach a narrative label, threshold, or regime statistic to any series
- Compute `ρ_k,t` from any input with `available_time > t`
- Touch the holdout
