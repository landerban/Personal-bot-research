# Stage G3-C-SPEC (v4) — Vintage integrity, then the re-lock. Still no run.

Withdraws a false claim in §70.8 — that delaying an unverified current-vintage
value prevents leakage — and replaces the fallback with a mechanical rule
frozen **before** any reconstruction is attempted.

**No forecast fitted on real data. No return-based result. No trial consumed.
Gen-3 0 of 20. Holdout sealed.**

Appended as `NOTES` §70.9 (append-only; §70.0–§70.8 unedited). Supersedes
§70.8's fallback and lock on completion.

---

## 1. The withdrawn claim, and why it was false

§70.8 stated that serving a current-vintage value at publisher-time-plus-one-
business-day "cannot leak." **It can, and the §70.8.1 audit proved it.**

A value has two coordinates, and delaying one does not repair the other:

```
DTWEXBGS 2020-01-02
  2020 vintage (what was knowable then) : 115.0172
  2021 vintage                          : 115.0169
  today's 2026 archive                  : 114.9745   ← what the loader holds

§70.8 fallback: serve 114.9745 stamped 2020-01-03 22:00
                → a 2026 recomputation delivered to a January 2020 decision,
                  one day late. Still the wrong number.

delay fixes  : WHEN a value was knowable
vintage fixes: WHICH value was knowable
```

**Frozen rule, recorded verbatim:**

> **A timestamp delay cannot substitute for vintage integrity.** For any series
> whose current historical values are not proven equivalent to historical
> publisher values, current-vintage observations **may not be exposed at
> historical decision times**. Such a series must either be reconstructed from
> point-in-time vintages — each value timestamped no earlier than that
> vintage's actual availability — or be **unavailable to the model**. No
> imputation, zero-filling, current-vintage substitution, proxy substitution,
> or replacement factor is permitted.

Note for the record: the §70.8 audit produced the evidence that condemned the
§70.8 fallback. That is the apparatus working, not failing.

## 2. Three provenance states, replacing the binary

```
VALUE_EQUIVALENT     current archive proven equivalent to historical vintages
                     ⇒ current archive may be served at historical possession times
                       (t_usable = max(publisher, 22:00 fetch))

PIT_RECONSTRUCTED    not equivalent, but historical as-of vintages reconstructed
                     ⇒ serve the actual PIT vintage per §3

UNAVAILABLE          neither equivalence nor valid PIT reconstruction exists
                     ⇒ the feature does not exist for Trial 1
```

Current status carried forward from §70.8.1: `fred_DGS2`, `fred_DGS10`,
`cboe_VIX` = **VALUE_EQUIVALENT**. `fred_DTWEXBGS`, `fred_NASDAQ100`,
`fred_SP500` = pending §3.

## 3. The reconstruction and inclusion algorithm — FROZEN BEFORE EXECUTION

This section is committed **before** any reconstruction is attempted, so its
outcome is mechanical rather than negotiated series by series.

### 3.1 The reconstructed object

A revision store, not a value table:

```
observation_date | vintage_available_time | value
```

and the PIT query at each historical decision:

```sql
WHERE vintage_available_time <= decision_time
ORDER BY vintage_available_time DESC
LIMIT 1
```

i.e. *the latest vintage of this observation knowable by 22:00 UTC on that
historical day* — never today's value for that observation date.

**Vintage timestamping, frozen:** if the vintage service supplies a **date but
not a publication time**, do **not** invent an intraday time. Treat that
vintage as available from the **first 22:00 UTC fetch strictly after** the
vintage date. Conservative, delay-only, consistent with §68.11's
anti-lookahead principle.

### 3.2 The procedure, per series

1. Enumerate available as-of vintages across the development window at
   sufficient density that every decision date has a preceding vintage.
2. Build the §3.1 store; each row carries its source and hash.
3. **Integrity requirements** (fixed now): every decision date in
   2020-01-01 → 2024-12-31 must resolve to some vintage row; the honesty
   guard of §70.8.0 applies to every response used; no row may be interpolated,
   forward-filled across a gap, or synthesised.
4. **Coverage requirement:** 100% of decision dates resolve. Anything less ⇒
   `UNAVAILABLE`. No partial-coverage series enters M1.

### 3.3 Nasdaq — no after-the-fact tolerance

§70.8.1's discrepancies sit at the third decimal, plausibly ALFRED's serving
precision changing. **A tolerance derived from discrepancies already observed
is an after-the-fact tolerance**, however reasonable it looks. Two admissible
routes only:

- **Reconstruction** per §3.2 (preferred, requires no new rule); or
- a **precision-aware comparison whose rule comes from source semantics** —
  comparison at the officially published precision of the source *at that
  historical date* — pre-registered in its own ledger entry **before**
  re-running. If historical source precision cannot be established from
  documentation, this route is closed.

### 3.4 SP500 — no substitute may appear

§70.8.1 found **0% vintage coverage**: no archive passes the honesty guard,
none is held. Admissible: procure a legitimate historical archive or direct
publisher source under a frozen acquisition/verification protocol, then §3.2.
Otherwise **UNAVAILABLE**.

**No substitute equity index may be introduced in v4.** Swapping in another
index would change the hypothesis, and would require its own pre-registration.

### 3.5 The contraction rule — frozen, mechanical

> For each pre-registered M1 exogenous component, attempt the §3.2 procedure.
> If it yields valid PIT observations meeting §3.2's integrity and coverage
> requirements, the component **remains**. Otherwise it becomes `UNAVAILABLE`
> and is **removed from Trial-1 M1**. No zero-filling, current-vintage
> substitution, proxy substitution, or replacement factor.

Whatever falls out is the result. **This is not feature selection:** no
forecast has been fitted and no Q result seen. It is the discovery that a
sensor lacks historical records. The distinction is recorded because the two
look identical from the outside and are opposite in epistemics.

If the contraction changes M1's dimensionality, the feature specification is
re-locked with the reduced family list stated explicitly.

## 4. The pinning test — the exact bug, made impossible

```
given   historical vintage = 110.10
        current archive    = 110.35
        decision_time      = historical date + 1 business day

assert  110.35 is NEVER returned
        (110.10 if its PIT vintage is held; otherwise MISSING/UNAVAILABLE)
```

Plus: a series marked `UNAVAILABLE` is unreadable at any timing; a series
marked `PIT_RECONSTRUCTED` reads only from the revision store, never from the
current archive; `VALUE_EQUIVALENT` retains §70.8's behaviour.

## 5. Re-lock

On completion §70.9 supersedes §70.8: the loader (per-series provenance
enforcement), the manifest (three-state statuses), the revision stores and
their hashes, `g3/features.py` if the family list contracted, and everything
§70.8 pinned unchanged. §70.8's LOCK lines struck as superseded, retained.

Trial 1 remains `pre-registered`, `attempt_id = 1`, `valid_trial_count = 0`.
Seed derived from the **new** lock commit, printed at run time.

## Order of work

1. §70.9 §§1–4 appended, dated — **including §3 in full, before any
   reconstruction is attempted**
2. Execute §3.2 for DTWEXBGS, NASDAQ100, SP500; record evidence verbatim
   (vintage counts, coverage, sources, hashes, guard results)
3. Apply §3.5 mechanically; state each series' final state
4. Implement §4's tests; per-series enforcement green
5. Re-lock; strike §70.8's lines; report
6. **STOP.** Both delegates review §70.9.

## Acceptance

- §1's withdrawal and the vintage-integrity rule recorded verbatim
- Three-state provenance implemented; no series remains "UNVERIFIED"
- §3 committed **before** reconstruction; vintage-timestamp conservatism
  frozen; 100% coverage required
- Nasdaq handled by reconstruction or a source-semantics rule pre-registered
  separately — no observed-discrepancy tolerance
- No substitute index introduced
- §3.5 applied mechanically; any contraction recorded as data availability,
  not feature selection
- §4 tests green, including the exact 110.35-never-returned case
- Re-lock complete; **Gen-3 0 of 20; holdout sealed; no return read beyond
  pre-registered vintage comparisons**

## Do not

- Serve a current-vintage value at a historical decision time for any series
  not `VALUE_EQUIVALENT`
- Invent an intraday time for a date-only vintage
- Derive a precision tolerance from discrepancies already observed
- Substitute a proxy, another index, or a filled value for anything
  `UNAVAILABLE`
- Accept partial vintage coverage
- Treat the contraction as a performance-driven choice, or revisit it after
  any Q result
- Run G3-C, or touch the holdout
