# Stage G3-A3 — Eight corrections before G3-B

Closes the remaining ambiguities in §68 identified by the first delegate and
accepted by the second. Three of the eight correct rules the second delegate
wrote or endorsed. Appended as `NOTES` §68.12 (append-only; §68.0–§68.11
unedited), except §68.0 which receives a **correction pointer**, not an edit.

**No forecast fitted. No return-based comparison. No G3-B measurement. No
trial consumed. Gen-3 0 of 20. Holdout sealed.**

---

## 1. Publisher vs aggregator — the source chain must not collapse

If the bytes come from FRED, the Fed's 16:15 ET H.15 release is
`underlying_public_time`; **FRED's own serving time is
`source_available_time`**, and access is governed by the latter (§68.11.1.2).
Calling FRED "primary" while using the Fed's release rule conflates the two.

Add to every manifest entry:

```
publisher         — who originates the value (Federal Reserve, CBOE, ICE, …)
retrieval_source  — from whom we actually obtain the bytes (FRED, CBOE, vendor)
```

Two admissible resolutions per series, recorded per series:
- **Pull from the publisher** (e.g. the Fed's H.15 release) and legitimately
  use its release rule as `source_available_time`; or
- **Keep the aggregator** and establish a **conservative aggregator-
  availability rule** (an assumed lag that can only delay, never advance,
  availability — the §68.11.1 anti-lookahead principle).

Applies equally to H.10/broad dollar and the FRED index proxies. A test
asserts no series has `publisher == retrieval_source` unless it genuinely does.

## 2. Unknown is not estimated — provenance follows the same rule

Replace the inferred-bound encoding with:

```
retrieved_at_utc            = null
retrieved_at_upper_bound_utc = <commit d8820ca timestamp>
retrieval_time_quality       = "upper_bound"
```

A commit timestamp must never occupy a field that later code will read as an
observed acquisition time. The corrected tool populates true values on any
future retrieval; nothing is back-filled.

## 3. Remove "minimum activity level" as a kill criterion (§68.10)

**This corrects a rule the second delegate wrote.** §68.10's minimum activity
level, "derived from expected conviction-day frequency," reintroduces a
formation requirement under a new name — the precise invariant Gen-3 exists to
remove (§68.1). **If the correct model finds two trades in a month, two trades
are correct.**

Replace with:
- **Activity rate and expected trade count → DISCLOSURE**, reported beside
  every result, never a gate.
- Where observations are too few to resolve the hypothesis at the frozen
  precision: **INDETERMINATE, not FAIL.**
- A **mechanical feasibility** test remains admissible (minimum notional,
  liquidity, execution viability). "The strategy must trade often enough" does
  not.

## 4. Demote the probability threshold to an illustration (§68.1)

**Corrects a formula the second delegate derived.**
`|P(up) − 0.5| > c_rt / (2·E|r|)` holds only under payoff symmetry, and
contradicts the adjacent rules that allocation uses the predicted return
distribution with asymmetric tails (§68.2.1, §68.2.3).

- Demoted to a **special-case illustrative derivation**, explicitly not
  governance.
- **The general gate:** `E[r_i | F_t] − C_i − required_risk_compensation > 0`.

## 5. Invalidation: a path statistic, and one health metric among several

**Corrects two claims the second delegate endorsed.**

- **The path variable.** A terminal-return quantile is not a valid intratrade
  barrier. Invalidation must be defined against an explicitly forecast **path
  statistic** — maximum adverse excursion, barrier-hit probability, or another
  named quantity — fixed in the spec stage. `r_path < Q_α(F̂_entry)` as written
  is withdrawn pending that definition.
- **"IS" → "is a primary."** Invalidation coverage is *a primary* model-health
  statistic, not the entire health state. Calibration drift, distribution
  shift/OOD, ensemble disagreement (if used), tail severity, and correlated
  simultaneous failures may carry independent information. The composite health
  rule is **deferred** to the spec stage, not asserted here.

## 6. Cold-start sequencing depends on Q3 alone (§68.9)

The hierarchical juvenile rung answers *can cross-sectional forecasting extend
to insufficient-history names?* Its prerequisite is that the mature-name
cross-section works at all — **Q3 PASS**. Q4 (incremental exogenous
cross-sectional skill) determines only **whether M1 exogenous features are
inherited into that rung**, not whether the rung may run. If Q3 passes and Q4
fails, a crypto-native + chain/sector hierarchical cold-start model remains
testable.

## 7. The ladder needs an explicit INDETERMINATE branch (§68.11.4.2)

FAIL and INDETERMINATE are different under this project's governance. Freeze:

| Cheap-rung outcome | Expensive rungs (corporate / congressional / news-LLM) |
|---|---|
| ≥ 1 PASS | may be **proposed** (each on its own pre-registration) |
| No PASS, ≥ 1 INDETERMINATE | **DEFERRED**, not rejected |
| Both FAIL | **BLOCKED** under the current generation |

An unresolved cheap rung must not permanently block the expensive ones, and a
merely-deferred state must not be read later as a pass.

## 8. §68.0 carries the Gen-2 correction itself

§68.0 says Gen-1/2 "both died on fixed invariants." §68.11.4.4 corrects this
200 lines later; a reader who stops at the design signature learns the wrong
history. Append a **correction pointer** to §68.0 (do not edit it):

> **Correction (§68.12.8):** both suffered fixed-invariant formation failures;
> **Gen-2 additionally and independently failed at the signal level** —
> RCM v1's residual-momentum IC was −0.0115 with its entire CI below zero,
> independent of breadth, capital, solver behaviour, and formation. See
> §68.11.4.4.

## 9. Non-blocking note — leave-one-out scope (§68.3)

Recorded, not changed: **LOO group priors remain frozen for the cold-start
evaluation rung**, where they are the correct conservative benchmark rule. In
a properly specified hierarchical model, using an asset's own **past PIT**
observations to estimate shared hyperparameters is not inherently lookahead;
the real hazards are future information and double-counting the same
observation in both prior estimation and asset likelihood. The **production
hierarchical estimator is separately specified later**; this note prevents the
benchmark rule from being mistaken for a prohibition on hierarchical
estimation generally.

---

## Order of work

1. §68.12 appended, dated, containing items 1–9; §68.0 receives the §8
   correction pointer only (no edit); §68.1–§68.11 unedited
2. Manifest: `publisher` / `retrieval_source` fields; per-series resolution
   recorded (publisher-pull or conservative aggregator rule); the §2 retrieval
   encoding; tests green
3. Report. **Stop before G3-B**, whose protocol is frozen in the ledger before
   any reading.

## Acceptance

- `publisher` and `retrieval_source` distinct per series, with each series'
  availability resolution recorded and only-delay-never-advance preserved
- `retrieved_at_utc = null` + upper bound + quality flag; no commit timestamp
  in an observed-time field
- §68.10 activity criterion replaced by disclosure + INDETERMINATE; mechanical
  feasibility retained
- Probability threshold demoted to illustration; general gate recorded
- Invalidation redefined against a path statistic (definition deferred) and
  downgraded to *a primary* health metric; composite rule deferred
- Cold-start gated on Q3 PASS; Q4's role stated
- Ladder INDETERMINATE → DEFERRED branch frozen
- §68.0 correction pointer present
- LOO scope note recorded
- **No forecast, no measurement, Gen-3 0 of 20, holdout sealed**

## Do not

- Collapse publisher and retrieval_source, or use a publisher release rule for
  aggregator-sourced bytes
- Put an inferred bound in an exact timestamp field
- Reintroduce any minimum trading frequency as a gate
- Use the probability-threshold formula as governance
- Treat invalidation coverage as the whole model-health state
- Block expensive rungs on an INDETERMINATE cheap rung
- Edit §68.0–§68.11; run G3-B; fit any model; touch the holdout
