# Stage G3-C-SPEC (v3) — Vintage provenance, then the re-lock. Still no run.

Closes the last delegate hold: §70's timing fix corrected **when** the bot
could have known a value and said nothing about **what value it would have
seen**. Those are separate leaks; only one was closed. This stage verifies the
second **per series**, splits the availability-quality label, and re-locks.

**No forecast fitted on real data. No return-based result. No trial consumed.
Gen-3 0 of 20. Holdout sealed.**

Appended as `NOTES` §70.8 (append-only; §70.0–§70.7 unedited). Supersedes
§70.7's lock on completion.

---

## 1. The defect, stated exactly

Today's FRED archive supplies the **current vintage** of each historical
observation. §70.6 assigns those bytes a historical availability time derived
from the publisher's release schedule. If a value was later revised, the model
receives a number that did not exist at the timestamp it is stamped with.

```
historical bytes    = FRED archive downloaded in 2026 (current vintage)
assumed timing      = publisher release + 22:00 possession rule   ✅ correct
assumed VALUE       = "what the publisher showed then"            ❓ UNVERIFIED
```

Timestamp arithmetic flawless; the value potentially wrong. **Vintage
leakage.** §68.11's `revision_policy` and `vintage_support` fields anticipated
this; §70 did not use them.

**Scope, recorded:** this affects the **exogenous panel only**. BTC/ETH bars
and funding come from the PIT store, are exchange-settled, and do not revise —
so `M0-dir` and `M0-xs` are unaffected. **Q1 and Q3 are unaffected by this
defect.** Only `M1` depends on the resolution.

## 2. Availability quality — split the label

§69 defined `"observed"` as genuine historical serving timestamps or a direct
publisher feed. §70 applied it to a **documented schedule**, which is weaker
evidence. Restore the distinction:

```
source_availability_quality = "documented_schedule"
  publication_schedule = "16:15 America/New_York"      (per source)
  usable_time_rule     = "first scheduled acquisition ≥ publication"

source_availability_quality = "observed"               (not yet held for any series)
  actual_publication_timestamp = …
  actual_retrieval_timestamp   = …
```

`"observed"` is reserved for evidence we actually possess. Every adopted
exogenous series is re-labelled `"documented_schedule"` today.

## 3. Per-series vintage verification — the work of this stage

**No blanket policy.** A closing price and a re-weightable index have different
failure modes: an index's history can be recomputed by a re-basing without any
single day ever being "revised."

For each adopted exogenous series, establish and record:

```
historical_value_source     = FRED | CBOE | …
production_source           = Federal Reserve | CBOE | …
revision_policy             = <as documented by the publisher>
vintage_support             = <does an archive of original vintages exist?>
publisher_value_equivalence = VERIFIED | UNVERIFIED
verification_method         = <what was actually compared>
verification_evidence       = <sample dates, values, source URLs, hashes>
```

**Verification means comparison, not assertion.** Acceptable methods, in order
of strength:

1. **Vintage archive comparison** — e.g. ALFRED's as-of-date vintages for FRED
   series: draw a pre-registered sample of development-window dates, compare
   the original vintage against today's value, record the discrepancy rate and
   magnitude.
2. **Independent publisher archive** — e.g. CBOE's own VIX history against the
   FRED mirror (§69 already showed these agree numerically under matched
   timing; that comparison is evidence and may be cited).
3. **Documented non-revision policy** plus a spot check against any
   independently archived source.

If none is achievable for a series, it is **UNVERIFIED**. Do not infer
equivalence from a series "probably not revising."

**Sample selection is pre-registered before comparison:** a fixed rule (e.g.
every Nth business day across the development window, plus all dates within
five business days of an FOMC or major macro release, where such a calendar is
already available). Do not choose dates after seeing discrepancies.

## 4. The mixed timing rule — the outcome of §3

```
publisher_value_equivalence = VERIFIED    ⇒ publisher-time reconstruction
                                            (t_usable = max(publisher, 22:00 fetch))

publisher_value_equivalence = UNVERIFIED  ⇒ retain the CONSERVATIVE FRED rule
                                            (publisher + 1 business day, §68.12.1)
```

**Unverified series are not dropped.** They run handicapped — costing
information, as §69 measured — but **cannot leak**. This is strictly better
than either blanket option: no sacrificed information where the value provably
did not change, no leakage where it might have.

The manifest records the rule applied per series, and the loader enforces it
per series (a test asserts a series marked UNVERIFIED cannot be read at
publisher timing).

## 5. Expected outcome, and what it does not license

Whatever §3 returns is recorded as measured. Two things are fixed in advance:

- **A discrepancy rate is reported, never thresholded into a pass by
  judgement after the fact.** If a series shows revisions, it is UNVERIFIED —
  the conservative rule applies, and the fact is recorded rather than argued
  around.
- **Verification status may not be revisited after any Q1–Q4 result.** A
  series that runs handicapped through Trial 1 stays handicapped for that
  trial's record, whatever the outcome.

## 6. Non-blocking, recorded not adopted — information age

The exogenous convention uses the two most recent distinct observations, so
Friday's equity move remains the "latest 1-day return" through the crypto
weekend. That is what the live reader genuinely possesses, and it is correct.
The model does not currently know whether an observation is 2 hours or 50
hours old.

**Not added now.** §69.2 already measured the staleness profile (25–30 stale
days per 90-day window), so the phenomenon is documented; expanding the frozen
feature set immediately before Trial 1 is precisely what the lock exists to
prevent. Recorded as a **candidate for a later pre-registered improvement**,
in the improvement ledger, not in `M0`/`M1`.

## 7. Re-lock

On completion, §70.8 supersedes §70.7's lock:
- the manifest (with per-series verification status and timing rule),
- the loader's per-series enforcement,
- everything §70.7 pinned, re-hashed where changed.

Trial 1 remains `pre-registered`, `attempt_id = 1`, `valid_trial_count = 0`.
Seed rule unchanged: derived from the **new** lock commit hash, printed at run
time, never predicted.

## Order of work

1. §70.8 appended, dated, with §3's sample rule **fixed before any
   comparison**
2. Per-series verification executed; evidence recorded verbatim (dates,
   values, sources, discrepancy counts)
3. Manifest and loader updated with the §4 mixed rule; per-series enforcement
   test green; the availability-quality re-labelling applied
4. Re-lock; hashes recorded; §70.7 lines struck as superseded (retained, not
   deleted)
5. **STOP.** Both delegates review §70.8. G3-C remains a separate stage.

## Acceptance

- §1's scope recorded (exogenous only; Q1/Q3 unaffected)
- Availability-quality split applied; **no series labelled `"observed"`**
  without actual timestamps
- Per-series verification with method, evidence, and discrepancy counts —
  no blanket policy, no inferred equivalence
- Sample rule pre-registered before comparison
- §4 mixed timing implemented and enforced per series, with a test
- §5's two prohibitions recorded
- §6 recorded as a deferred candidate, **not** added to the feature set
- Re-lock complete; §70.7 struck-not-deleted; trial still `pre-registered`
- **No return read; no forecast fitted on real data; Gen-3 0 of 20; holdout
  sealed**

## Do not

- Apply one revision policy to all six series
- Infer equivalence from "probably doesn't revise"
- Choose verification sample dates after seeing discrepancies
- Label a documented schedule as `"observed"`
- Drop an unverified series — handicap it instead
- Revisit verification status after any Q result
- Add an information-age feature, or any feature, before Trial 1
- Run G3-C, or touch the holdout
