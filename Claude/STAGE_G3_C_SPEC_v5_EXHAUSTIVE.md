# Stage G3-C-SPEC (v5) — Exhaustive provenance, final re-lock, then RUN

The last provenance stage. Replaces sampled equivalence with exhaustive
reconstruction for the three remaining series, re-locks, and **closes
specification review**. No model or statistical decision is reopened.

**No forecast fitted on real data. No return-based result. No trial consumed
by this stage. Gen-3 0 of 20. Holdout sealed.**

Appended as `NOTES` §70.10 (append-only; §70.0–§70.9 unedited). Supersedes
§70.9's lock on completion.

---

## 1. The residual gap

`fred_DGS2`, `fred_DGS10`, and `cboe_VIX` are `VALUE_EQUIVALENT` because a
pre-registered 132-date sample found zero discrepancies (§70.8.1). Against the
principle v4 established — *the historical value itself must be PIT-correct* —
that is not the same claim:

```
"132 sampled dates were clean"   ≠   "every value the model uses is historically identical"
```

~1,250 business days lie in the development window; ~1,100 were never
compared. The sample was decisive for Nasdaq (34.5% revision rate — a clean
132-sample was essentially impossible, `P ≈ 6e-25`) but a **rare**-correction
series is exactly what it cannot certify:

| true revision rate | P(clean 132-sample) | contaminated dates among 1,250 |
|---|---|---|
| 0.5% | 52% | ~6 |
| 1.0% | 27% | ~12 |
| 2.0% | 7% | ~25 |

H.15 does issue corrections. The dense ALFRED machinery built in §70.9.5 makes
this cheap to close: API calls and disk, not design.

## 2. Exhaustive reconstruction

Run the **frozen §70.9.3 procedure unchanged** — same as-of density, same
honesty guard, same conservative stamping (a changed value becomes available
at the first 22:00 UTC fetch strictly after its as-of date), same 100%
coverage requirement — for:

- `fred_DGS2`
- `fred_DGS10`
- **VIX**: reconstruct PIT `VIXCLS` and compare **exhaustively** against every
  CBOE close the model would use across the development window. (CBOE's own
  file is a single archive with no vintage service; the exhaustive comparison
  against reconstructed VIXCLS vintages is the available evidence.)

**Expected outcome, stated so it is not read as a finding:** if these series
genuinely do not revise, their stores collapse to one row per observation.
That is the hypothesis, not the assumption — the run reports what it finds.

## 3. The label, tightened

```
VALUE_EQUIVALENT   reserved for: ALL model-used historical observations
                   verified equivalent (exhaustive), not "a sample found none"
PIT_RECONSTRUCTED  serves the revision store only
UNAVAILABLE        the feature does not exist for Trial 1
```

Any series whose exhaustive check reveals revisions moves to
`PIT_RECONSTRUCTED` (its store already exists from §2) — **mechanically, by
the §70.9.3 rule, no judgement.** Any series that cannot be exhaustively
established becomes `PIT_RECONSTRUCTED` if a valid store exists, else
`UNAVAILABLE`. The §70.9.3.5 contraction rule applies unchanged if a series
falls out; no substitute may appear.

After this stage every exogenous value entering Trial 1 carries exactly one of
two guarantees:

```
historically PIT-correct value      |      UNAVAILABLE
```

## 4. Re-lock, and the trial

Re-lock the loader, manifest (with tightened states and any new stores), the
new revision stores and hashes, `g3/features.py` if the family list contracted
again, and everything §70.9 pinned unchanged. §70.9's LOCK lines struck as
superseded, retained.

Trial 1 remains `pre-registered`, `attempt_id = 1`, `valid_trial_count = 0`.
Seed derived from **this** lock commit, printed at run time, never predicted.

## 5. Specification review closes here

Recorded as a standing rule for Gen-3:

> **G3-C-SPEC review is closed at this lock.** No further pre-trial
> specification amendment is made unless an **implementation test actually
> fails**. Review that continues past the point where tests pass becomes its
> own source of specification drift, and the architecture has earned the right
> to be tested. Both delegates confirm §70.10 or name a failing test; there is
> no third option.

Post-run, the §70.4/§70.6 consequences table governs — including the
bug-versus-design rule, which requires a **reproducing test against the
pre-fix code**, not an argument.

## Order of work

1. §70.10 §§1–3 appended, dated, **before** the reconstruction runs
2. Execute §2; report evidence verbatim (as-of counts, failed dates, raw and
   normalized rows, real revisions with magnitudes, coverage, store hashes)
3. Apply §3 mechanically; state each series' final state; apply §70.9.3.5 if
   anything falls out
4. Re-lock per §4; strike §70.9's lines
5. Append §5 verbatim
6. **STOP for delegate confirmation.** Then G3-C runs as a separate stage,
   consuming Gen-3 trial 1 of 20.

## Acceptance

- §1's gap recorded with the sample-power arithmetic
- §70.9.3 procedure run **unchanged** on DGS2, DGS10, VIXCLS/VIX; exhaustive
  VIX comparison across the full development window
- Every series ends `VALUE_EQUIVALENT` (exhaustive), `PIT_RECONSTRUCTED`, or
  `UNAVAILABLE` — no sampled-equivalence state survives
- Contraction applied mechanically if triggered; no substitute introduced
- §70.9.4 pins still green; re-lock complete; trial still `pre-registered`
- §5 recorded verbatim
- **No return read beyond pre-registered vintage comparisons; no forecast
  fitted on real data; Gen-3 0 of 20; holdout sealed**

## Do not

- Reopen any model, feature, criterion, or statistical decision
- Alter the §70.9.3 procedure while re-running it
- Retain any sampled-equivalence classification
- Introduce a substitute for anything that falls out
- Amend the specification further absent a failing test
- Run G3-C in this stage, or touch the holdout
