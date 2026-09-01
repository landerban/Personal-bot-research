# Stage G3-A2 — Audit, source-policy, and governance corrections

Fixes the two PIT hazards and five audit defects found in G3-A, repairs four
surviving G3-0 v2 governance issues, and records a licensing incident. Both
delegates converged on every item below.

**No forecast fitted. No return-based comparison. No G3-B measurement. No
trial consumed. Gen-3 0 of 20. Holdout sealed.**

Appended as `NOTES` §68.11 (append-only; §68.0–§68.10 unedited) plus the
manifest and tooling changes described.

---

# PART I — PIT hazards (both are lookahead)

## I.1 CRITICAL — timezone/DST: never store a UTC constant

The manifest hardcodes `16:15 ET → 20:15 UTC`. That holds only under US
daylight time; in winter the true release is **21:15 UTC**. A model reading at
20:30 UTC in January would consume a release that has not happened —
**one hour of lookahead for ~4.5 months of every year**, invisible at daily
resolution and destructive in the intraday branch.

Replace every hardcoded UTC release time with:

```
release_timezone      = America/New_York
release_local_time    = 16:15            (per source)
release_calendar      = FederalReserveBusinessCalendar | CboeCalendar | NyseCalendar
```

converted per-date with `zoneinfo`. A test asserts a January and a July
observation of the same source resolve to different UTC instants, and that no
`"20:15Z"`-style constant survives anywhere in the manifest or loader (grep
test).

## I.2 CRITICAL — four timestamps, and the one that governs access

"The index closed" and "our source published it" are different events. Record
per observation:

```
observation_time        — the economic period the value describes
underlying_public_time  — when the underlying value first became public anywhere
source_available_time   — when THIS staged source first served it
retrieved_at_utc        — when our system actually obtained/archived this copy
```

**Access rule, frozen:** a model may consume a value only when
`source_available_time ≤ t`. If the staged source is FRED, the model may not
act on a cash-index close hours before FRED served it, however public the
underlying was. If production later swaps to a direct feed, **that feed's**
availability timestamp governs and the manifest entry is re-derived.

`retrieved_at_utc` answers a different question (live auditability) and never
substitutes for `source_available_time` in historical research.

**Revisable series** (macro releases entering later rungs) additionally
separate `revision_id` / vintage from first publication:
`observation → publication → revision → our retrieval`, four distinct fields.
Recorded now so CPI/PCE/NFP cannot be added later without it.

# PART II — Source policy and provenance

## II.1 Panel split — Panel A and Panel B are different data problems

**Panel A — daily baseline:** BTC/ETH + carry, daily VIX, daily rates, a daily
USD measure, cash equity indices, daily gold. **Status: the audit is
sufficiently complete to *specify* a daily baseline, subject to explicit
source-adoption decisions.** Available ≠ adopted.

**Panel B — event-driven / intraday:** requires time-resolved instruments —
ES, NQ, a rates proxy, DX/DXY or a real-time USD proxy, VIX/VX if justified,
GC. **Cash indices are not substitutes here:** they carry no Globex session,
and the US-close → Asia → Europe → next-US-open window is precisely what a
24/7 book would exploit.

Procurement therefore reframes from *"do we need ES?"* to **"do we activate
the intraday/event-driven branch?"** — if yes, futures-quality data is
necessary, not optional.

## II.2 Adoption decisions, listed not made

Record as open, for the spec stage with delegate review:
`DTWEXBGS` broad-dollar in place of ICE DXY (different basket — record the
difference); FRED cash SP500/NASDAQ100 in place of ES/NQ for Panel A only
(overnight-gap limitation recorded); gold included at all, and from where.
G3-A2 **does not adopt any of them.**

## II.3 Gold source — unverified

Change the manifest entry to `candidate_source: Nasdaq Data Link LBMA/GOLD`,
`verification_status: UNVERIFIED`, until a key actually returns data. The
audit's confident phrasing is withdrawn.

## II.4 Manifest provenance

Per series add: `retrieved_at_utc` (timezone-aware, replacing `date.today()`),
HTTP `Last-Modified`, `ETag`, status code, source timezone, publication rule,
`revision_policy`, `vintage_support`, and a `licence_class`
(`public_domain | redistribution_restricted | licensed`).

# PART III — The licensing incident (record, then remediate separately)

FRED's S&P 500 and NASDAQ-100 series are redistribution-restricted; raw CSVs
were committed to a public repository in `d8820ca`.

**Immediate (this stage):**
- stop tracking: `git rm --cached` the restricted CSVs; `data/exogenous/raw/`
  added to `.gitignore`
- keep in Git: the downloader, the manifest, source identifiers, retrieval
  timestamps, SHA-256s, coverage statistics, licence classification
- default policy recorded: **vendor/index raw data is never committed**;
  public-domain series may be, by explicit classification

**Deliberately NOT done here:** purging the blobs from public Git history.
A `filter-repo` rewrite changes every commit SHA, and **this project uses
commit hashes as governance locks** (§63.6, §64, §68 lock references). Rushing
it would silently invalidate the audit trail that the ledger's evidentiary
value rests on.

Record as **an open remediation item with its own governance note**, to be
decided separately: whether to rewrite history, and if so how recorded lock
hashes are re-anchored (e.g. a mapping table appended to the ledger before the
rewrite, so every historical lock remains resolvable).

# PART IV — Governance repairs to §68 (append as §68.11.4)

## IV.1 Q2/Q4 false-PASS — conjunctive criterion

As written, `BSS(M0) = −0.20, BSS(M1) = −0.10` would "support the exogenous
thesis" while both models lose to climatology. Require, with the frozen
confidence criterion on **both**:

```
Skill(M1) > 0        AND        Skill(M1) − Skill(M0) > 0
```

Identical repair for the cross-sectional IC arm (Q4): `IC(M1) > 0` **and**
`IC(M1) − IC(M0) > 0`. "Made a bad predictor less bad" is not a usable
predictor.

## IV.2 The M1 hard gate is too strong — mechanism-specific ladder

Rule 8 blocked all further information rungs on cross-asset failure. Different
economic mechanism, different latency, different data. Replace with:

```
                  crypto-native baseline
                 ┌──────────┴──────────┐
          cross-asset rung      scheduled-macro rung      (cheap, independent)
                 └──────────┬──────────┘
        evidence of useful incremental exogenous value
                            ▼
          expensive rungs: corporate | congressional | news/LLM
```

A cross-asset failure does **not** forbid a scheduled-macro rung on its own
pre-registration. **If both cheap exogenous mechanisms fail**, the expensive
rungs are blocked — the kill-cheap-hypotheses-first principle preserved
without conflating mechanisms.

## IV.3 Feature budget — families capped AND transforms frozen

"≤ 8 features" was impossible as written (M1 = M0 + seven series). Replace:

> **≤ 8 feature families per model, and the transformation set within every
> family is separately frozen before the trial.** A family may not be expanded
> after observing OOS performance.

Example of a frozen family: `rates = {2Y level, 2Y Δ, 10Y level, 10Y Δ,
2s10s slope}` — not "whatever rate features later seem useful." Exact family
lists and their transform sets are fixed in the spec stage.

## IV.4 Gen-2 historical wording — preserve the independent rejection

§2.3's "not one was a signal failure" is true of the formation *events* and
misleading about Gen-2 overall. Append verbatim:

> Gen-1 and Gen-2 both suffered material portfolio-formation failures caused
> by fixed breadth/floor interactions at small capital. **Separately, Gen-2
> RCM v1 was decisively rejected at the signal level by negative
> residual-momentum IC (−0.0115, CI entirely below zero); that rejection was
> independent of breadth, capital, solver behaviour, and portfolio
> formation.** RCM would not have worked with dynamic breadth.

## Order of work

1. §68.11 appended, dated, containing Parts I–IV; §68.0–§68.10 unedited
2. Manifest and loader corrected per Part I; DST and no-UTC-constant tests
   green; four-timestamp fields populated for every staged series
3. Part II provenance fields; gold marked UNVERIFIED; panel split recorded;
   adoption decisions listed as open
4. Part III: untrack + gitignore + policy recorded; **history rewrite NOT
   performed**; remediation item opened with the lock-hash concern stated
5. Report. **Stop before G3-B.**

## Acceptance

- No hardcoded UTC release time anywhere (grep test); January/July resolve
  differently (test)
- Four timestamps per observation; `source_available_time ≤ t` is the access
  rule (test); revisable-series vintage fields defined
- Panel A/B split recorded; adoption decisions listed, none made; gold
  UNVERIFIED
- Manifest provenance fields incl. timezone-aware `retrieved_at_utc` and
  `licence_class`
- Restricted CSVs untracked and ignored; policy recorded; history rewrite
  explicitly deferred with the lock-hash rationale
- §68.11.4 records the conjunctive Q2/Q4 criterion, the mechanism-specific
  ladder, families-plus-frozen-transforms, and the Gen-2 wording verbatim
- **No forecast fitted; no G3-B measurement; Gen-3 0 of 20; holdout sealed**

## Do not

- Store any UTC constant for a release time
- Let `underlying_public_time` govern model access
- Adopt any substitute source in this stage
- Rewrite Git history in this stage
- Expand a feature family after seeing any result
- Run G3-B, fit any model, or touch the holdout
