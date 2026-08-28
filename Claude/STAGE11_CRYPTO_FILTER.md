# Stage 11 — The crypto-only universe amendment

Restores the universe rule to its intent — major **cryptocurrencies** — after
§47 found the volume-rank operationalization drifted into tokenised equities
and commodities. The amendment is legitimate because it is **provably inert on
all existing evidence** and grounded entirely in listing dates, which are
external to any return data. It is registered **before** the holdout.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials.** Holdout sealed
throughout — nothing here reads, runs, or touches 2025+ return data. Listing
dates and exchangeInfo metadata are not return data.

---

## 0. The fact this amendment rests on

The earliest non-crypto listing in the §47.1 table is XAUUSDT, **2025-12-11**.
Train ends 2023-12; validate is 2024. Therefore a crypto-only filter, applied
retroactively, selects the **identical universe on every day of train and
validate** — the filtered strategy and the tested strategy are the same
strategy everywhere evidence exists. The filter binds only from 2025-12-11
onward.

This is why no revalidation is needed: the amendment is a no-op on the past and
a restoration of intent for the future. **§3 proves the no-op in code rather
than asserting it in prose.** If the proof fails, the premise is wrong and the
stage stops.

---

## 1. Pre-registration — write into NOTES §48 before any code

Record, dated today:

1. The amendment: the universe rule becomes *"top 15 by point-in-time median
   quote volume, among crypto-asset perpetuals"* (definition in §2).
2. The grounds: §47's listing-date evidence — external to returns, discovered
   by a plumbing check, registered before the holdout.
3. The scope: binds from the date the first excluded instrument listed
   (2025-12-11); provably inert before that (§3).
4. What it is not: not a response to any return data, not a performance
   choice, not a holdout adjustment. The holdout rules (§29, §30, §37.5) are
   unchanged; the holdout will simply test the strategy the rules always
   intended.

## 2. The definition — operational, not a vibe

A symbol is **eligible** iff all of:

1. USDS-M perpetual, `status = TRADING`, quote asset USDT (per existing rules)
2. **Underlying is a native crypto asset.** Operationalise in layers:
   - exchangeInfo `underlyingType` / `underlyingSubType` where Binance
     provides a usable distinction — inspect what the fields actually contain
     for BTCUSDT vs XAUUSDT vs SNDKUSDT and document it in NOTES §48 before
     relying on it
   - a maintained `EXCLUDED_UNDERLYINGS` list seeded from §47.1: tokenised
     equities (SNDK, SKHYNIX, MU, SPCX), ETFs (SOXL), commodities (XAU, XAG,
     CL) — plus pattern classes: any perp whose underlying is an equity, ETF,
     fund, commodity, index on non-crypto assets, or fiat-referenced
     instrument
3. **Ambiguity → exclude and log.** If neither the metadata nor the list
   resolves a symbol confidently, it is out, with reason `underlying_ambiguous`
   in the universe log. The default is conservative because the next weird
   listing will not be as obvious as tokenised gold.

Stablecoin-underlying perps remain excluded as before. Crypto assets that are
merely *new* (HYPE, BANK) are **in** — recency was never the problem;
asset class is.

## 3. The no-op proof — the load-bearing step

Re-run point-in-time universe selection **with the filter** across
**2020-01-01 → 2024-12-31** and assert, day by day, that the selected top-15
(and the full eligible ranking, not just the top slice) is **bit-identical** to
the unfiltered record used by every logged run.

- Pass → the amendment is proven inert on train+validate; record the assertion
  output (days checked, zero diffs) in NOTES §48.
- **Any diff → STOP.** Report the day and symbols. It would mean a non-crypto
  instrument was in the historical universe after all, the §0 premise is wrong,
  and the amendment is no longer free — the decision returns to the user
  before anything else happens.

Add this as a permanent test (Test 26) so no future refactor can silently
break the equivalence.

## 4. The standing composition guard

§47's deeper lesson: a pre-registered rule can be broken by the world while
holding formally. Add to the live harness (and the daily paper report):

- At each universe build, log every symbol excluded by §2 with its reason and
  volume rank — visibility into what the filter is rejecting and how large it
  has become.
- **Alert** (dashboard AMBER + daily report line) when either: an
  `underlying_ambiguous` exclusion occurs, or excluded instruments would have
  occupied ≥ 3 of the unfiltered top-15. Both mean the market is moving under
  the rule again and a human should look.
- The guard observes and alerts; it never auto-amends anything.

## 5. Refreshed testnet coverage (Stage 10 §2.3, re-run)

§47.5's 47% coverage was measured against the **drifted** top-15; the eight
missing names are exactly the eight non-crypto ones. Re-run the coverage check
against the **crypto-only** top-15 as of the latest data:

- Report which of the 15 exist on testnet, and the resulting feasible N
- If coverage now supports N=10, the paper phase runs the full frozen config
  and the §47.5 reduced-N limitation is retired
- If still short, the reduced-N limitation stands as recorded — a venue
  constraint, not a tuning decision

Update NOTES §46.5 either way.

## 6. What this stage does not do

- Does not run, read, or touch any 2025+ return data. The holdout is exactly
  as sealed after this stage as before it.
- Does not modify the frozen strategy parameters — lookback, N, k, vol,
  capital, buffer all unchanged. Only universe *eligibility* is clarified.
- Does not re-validate anything: §3 proves nothing needs it.
- Does not decide the holdout. That remains the user's decision; this stage
  only ensures that when it is taken, it tests the intended strategy.

## 7. Order of work

1. §1 pre-registration into NOTES §48, dated, before code
2. §2 definition implemented; metadata behaviour documented
3. **§3 no-op proof — stop on any diff**
4. Test 26 added; full suites green
5. §4 guard wired into harness and dashboard
6. §5 coverage re-run; §46.5 updated
7. Report. Holdout untouched.

## 8. Acceptance

- §48 records amendment, grounds, scope, and non-purposes before implementation
- Definition uses metadata + seeded exclusion list; ambiguity excludes with log
- No-op assertion over 2020–2024 passed with zero diffs, output recorded —
  or the stage stopped and reported
- Test 26 permanent; all suites green
- Composition guard logging and alerting; no auto-amendment
- Refreshed coverage reported; paper N updated or limitation retained
- No 2025+ return data touched; budget **15 of 25**; holdout sealed

## 9. Do not

- Rely on the underlying-type metadata without first inspecting and
  documenting what it actually returns
- Resolve an ambiguous symbol by judgement call — exclude and log
- Proceed past a §3 diff
- Touch strategy parameters, 2025+ return data, or the holdout
- Let the guard auto-modify the universe rule
