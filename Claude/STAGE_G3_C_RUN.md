# Stage G3-C — RUN Gen-3 Trial 1

Execute the locked specification. **This stage consumes Gen-3 trial 1 of 20.**

Do exactly the nine steps below and nothing else. **No interpretation, no
feature change, no proposed fix, no follow-up analysis, no opinion about the
result.** The holdout remains untouched.

Governing: §68 (as amended), §69, §70 through §70.10 — the v5 lock. Both
delegates have confirmed §70.10 and cleared this run.

---

## 1. Verify the lock before anything is fitted

- Recompute the sha256 of every `LOCK-G3` line in §70.10.5 and confirm each
  matches: `g3/timing.py`, `g3/features.py`, `g3/models.py`,
  `g3/calibration.py`, `g3/sequential.py`, `g3/eval.py`, `rcm/eval_ic.py`,
  `tools/g3_exogenous_loader.py`, `data/exogenous/MANIFEST.json`, and the
  **five** vintage stores (DTWEXBGS, NASDAQ100, DGS2, DGS10, VIXCLS).
- Run the full test suite, including: the §70.9.4 vintage pins (a current
  value is never served historically; `UNAVAILABLE` unreadable at any timing;
  `PIT_RECONSTRUCTED` reads only from its store), the degeneracy pin
  (a shared additive term leaves cross-sectional IC bit-for-bit unchanged),
  the possession-rule and B.1 timestamp tests, the sequential-protocol test,
  and the evaluator determinism tests.
- Re-exercise the sealed-interval refusal and the 2024-12-31 range refusal;
  log both verbatim.

## 2. Stop condition

**If any hash mismatches or any test fails: STOP before fitting anything.**
Report the failing hash or test and nothing further. The trial is **not**
consumed by a pre-fit stop. Do not fix, do not proceed, do not run partially.

## 3. Seed

Derive `seed = int(sha256(lock_commit_hex)[:8], 16)` from the §70.10.5 lock
commit. **Print it.** It is never predicted, never chosen.

## 4. Execute

Log the trial `status: started`, `attempt_id = 1`, before the first fit. Then
run exactly the locked specification:

- sequential protocol: fit 2020 → forecast 2021; refit 2020–21 → 2022;
  2020–22 → 2023; 2020–23 → 2024
- `M0-dir` / `M1-dir` (18 features) and `M0-xs` / `M1-xs` (12 features), as
  locked; SP500 absent per §70.9.5
- regularisation selected inside training windows only, on the frozen grid,
  with the frozen folds, metric and tie-break
- Platt calibration fitted out-of-fold per §70.6
- features read through the locked loader; every value at its locked
  provenance state and timing

An errored run after this point consumes the attempt (§60.12 accounting).

## 5. Grade mechanically

| Q | Criterion |
|---|---|
| Q1 | `CI_lower(BSS_M0-dir) > 0` |
| Q2 | `CI_lower(BSS_M1-dir) > 0` **AND** `CI_lower(BSS_M1 − BSS_M0) > 0` |
| Q3 | `CI_lower(IC_M0-xs) > 0` |
| Q4 | `CI_lower(IC_M1-xs) > 0` **AND** `CI_lower(IC_M1 − IC_M0) > 0` |

Stationary bootstrap, 2,000 replicates, block length `n^{1/3}` recomputed per
series, the Gen-1 interval construction by hash, two-sided 90%, the §3 seed.
Undefined dates excluded, counted, reported with reason — never zero-filled.

Apply the criteria as written. Do not adjust, reinterpret, or add a
threshold.

## 6. Append the trial record

To `NOTES` §71 and `trials.jsonl`: seed, lock commit, per-question metric,
CI, verdict (PASS / FAIL / INDETERMINATE per the frozen definitions), and the
§70.6 D.3 descriptive block — Brier/BSS, reliability curve, probability-bin
counts, and realized subsequent returns by probability bin (mean and median).

Set `valid_trial_count = 1` unless the run is VOID under §60.12 (which
requires a defect **reproduced by a failing test against the pre-fix code** —
not an argument, and not to be attempted in this stage).

## 7. Report

Metrics, CIs, exclusion and coverage counts, and the four verdicts. Plus:
realized CI half-widths as observed resolving precision (§60.12.3 — not a
criterion), and the per-series provenance state actually used.

## 8. Report NOTHING else

No interpretation of what the numbers mean. No commentary on whether the
result is good, surprising, or expected. No proposed next stage, fix,
feature, or diagnostic. No comparison to Gen-1 or Gen-2. No recommendation.

**The consequences in §70.6 D.4 are already written and will be applied in a
separate review.** This stage's only job is to put the sealed measurement on
the record before anyone reacts to it.

## 9. Untouched

The holdout is not read. No 2025+ data is requested. The specification is not
modified by this stage under any outcome.

---

## Acceptance

- Lock hashes verified (all fifteen items incl. five stores); full suite
  green; both refusals logged — or the stage stopped at §2 with the failure
  named and the trial unconsumed
- Seed derived from the lock commit and printed
- Trial logged `started` before the first fit
- Locked specification executed exactly; no parameter selected outside its
  frozen procedure
- Four verdicts graded mechanically against §5
- §71 and `trials.jsonl` appended; `valid_trial_count = 1`
- Report contains numbers and verdicts only
- **Holdout sealed; specification unmodified**

## Do not

- Fit anything if §1 fails
- Alter any frozen quantity, feature, threshold, or procedure
- Interpret, recommend, diagnose, or propose
- Declare a defect without a reproducing test (and do not attempt one here)
- Read the holdout or any 2025+ data
