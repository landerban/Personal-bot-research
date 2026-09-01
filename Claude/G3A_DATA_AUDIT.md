# G3-A — Data audit and PIT policy (no trial)

Fulfils **STAGE_G3_0_GOVERNANCE.md §10 (G3-A)** and **§68.9**. Generated
2026-09-01.

> Governance mandate (§10 G3-A): *"For ES, NQ, VIX, US 2Y/10Y, DXY, gold, BTC,
> ETH: availability to 2020, granularity, cost, first-public timestamp
> semantics, and the exact observation usable at each decision time. Report
> what must be procured. **Stop if a required source is unavailable at any
> price**; report substitutes **without adopting them**."*

## Status of this document

- **No forecast fitted. No return-based result. No trial consumed. Holdout
  untouched.** (Governance header + acceptance.)
- Everything staged under `data/exogenous/` carries `"adopted": false` in
  `data/exogenous/MANIFEST.json`. **Adoption is deferred to the spec stage,
  behind delegate review** (Order of work step 4). Substitutes are staged so
  the audit is concrete; staging is not adoption.
- This audit does **not** append §68 to `NOTES.md`. That ledger write
  (Order of work step 1, with user decisions labelled) remains the owner's
  action and precedes any G3-C trial.
- **Nothing is unavailable at every price**, so §10's hard-stop does not fire.
  One instrument (gold) has **no clean free source reachable from this
  environment** and is flagged must-procure.

## Development / seal boundaries (governance §6)

- **Development window:** 2020-01-01 → 2024-12-31, sequential-in-time.
- **SEALED:** 2025-01 → 2026-07. Raw files below extend through 2026-08 because
  the seal is enforced **at model-read time (PIT views)**, never by truncating
  a raw archive. No fit may cross the seal.
- Every series carries a first-public / availability timestamp; **event date
  and availability date are never conflated** (§6).

---

## Per-instrument audit

Decision-time convention: a daily crypto decision is taken at some fixed UTC
cut. "PIT-usable at t" = the most recent observation whose **availability**
timestamp ≤ t. TradFi instruments do not trade weekends/holidays; the last
knowable value is carried forward with its own knowable-at stamp (staleness is
a state to model, not to hide).

| Instrument | §68.9 role | Source staged | Coverage | Cost | Status |
|---|---|---|---|---|---|
| **US 2Y** | M1 | FRED `DGS2` (Fed H.15) | 1990-01-02 → 2026-08-28 | free, public domain | ✅ primary, staged |
| **US 10Y** | M1 | FRED `DGS10` (Fed H.15) | 1990-01-02 → 2026-08-28 | free, public domain | ✅ primary, staged |
| **VIX** | M1 | CBOE `VIX_History.csv` (+ FRED `VIXCLS` cross-check) | 1990-01-02 → 2026-08-31 | free | ✅ primary, staged |
| **DXY** | M1 | *(substitute)* FRED `DTWEXBGS` broad USD | 2006-01-02 → 2026-08-28 | free | ⚠️ SUBSTITUTE only — true index must be procured |
| **ES** | M1 | *(substitute)* FRED `SP500` cash index | 2016-09-01 → 2026-08-31 | free (rolling 10y) | ⚠️ SUBSTITUTE only — future must be procured |
| **NQ** | M1 | *(substitute)* FRED `NASDAQ100` cash index | 1990-01-02 → 2026-08-31 | free | ⚠️ SUBSTITUTE only — future must be procured |
| **gold** | M1 | *(none reachable free)* | — | — | ⛔ MUST PROCURE |
| **BTC** | M0/M1 | Binance USDS-M perp, `xsmom.db` | 2020-01-01 → 2026-08-31 (2,404 daily bars) | free | ✅ in PIT store |
| **ETH** | M0/M1 | Binance USDS-M perp, `xsmom.db` | 2020-01-01 → 2026-08-31 (2,404 daily bars) | free | ✅ in PIT store |

### US 2Y / US 10Y — FRED DGS2 / DGS10 (primary, adoptable pending review)
- **Granularity:** daily, business days only; constant-maturity yield in
  percent. Non-trading days marked `.` (missing) in FRED CSV.
- **First-public timestamp:** the H.15 release is published each business day
  at **~16:15 ET for the _prior_ business day's trade date**.
- **PIT-usable at t:** value labelled date D is first knowable ~**D+1 16:15 ET
  (~20:15 UTC)**. Using D on day D would leak one release. Prior-period value
  only.
- **Cost/licensing:** free, US-government public domain; redistributable.

### VIX — CBOE VIX_History.csv (primary, adoptable pending review)
- **Granularity:** daily OHLC, history to 1990. FRED `VIXCLS` staged as a
  close-only cross-check.
- **First-public timestamp:** official daily **close** published by CBOE at end
  of the US cash session (**~16:15 ET / ~20:15 UTC**) for date D. Intraday VIX
  is disseminated live but only the close is captured here.
- **PIT-usable at t:** close for date D knowable ~D 20:15 UTC (**same day**, if
  the decision cut is after the US close; otherwise prior day).
- **Cost/licensing:** free public CSV from CBOE.

### DXY — SUBSTITUTE staged; true index MUST BE PROCURED
- **Blocker:** the **ICE U.S. Dollar Index (DXY)** is proprietary/licensed;
  no free authoritative daily feed. Stooq (`dx.f`) is behind a JavaScript
  proof-of-work anti-bot wall (unusable headless); Yahoo is rate-limited and
  ToS-restricted for redistribution.
- **Substitute staged (NOT adopted):** FRED `DTWEXBGS`, the Fed **nominal broad
  trade-weighted USD index** (H.10). Different basket/weights from DXY (adds
  CNY, MXN, etc.), daily, next-business-day release, starts **2006** (covers the
  dev window).
- **Procure if the exact index is required:** ICE/vendor DXY licence, or accept
  the broad-dollar substitute after delegate review with the basket difference
  recorded.

### ES / NQ — SUBSTITUTES staged; futures MUST BE PROCURED
- **Blocker:** the instruments named in §68.9 are the **CME E-mini futures**
  (ES, NQ), which are proprietary. Free proxies are the **cash indices**.
- **Substitutes staged (NOT adopted):** FRED `SP500` (S&P 500 cash; rolling
  ~10-year licence, so it starts 2016-09 and drops old history over time) and
  FRED `NASDAQ100` (Nasdaq-100 cash).
- **Material PIT caveat:** a cash index has **no overnight/Globex session**, so
  it misses every move between the US cash close (~16:00 ET) and a later crypto
  decision cut — precisely the window a 24/7 crypto book most needs. The future
  captures it; the cash index does not.
- **Procure if the overnight session matters:** CME/vendor ES & NQ continuous
  futures. Otherwise adopt the cash substitute after review with the
  overnight-gap limitation recorded.

### gold — MUST BE PROCURED (no clean free source reachable here)
- **Blocker:** the FRED LBMA gold series (`GOLDPMGBD228NLBM`,
  `GOLDAMGBD228NLBM`) now return HTML (discontinued IDs). Stooq (`gc.f`) is
  behind the JS anti-bot wall; Yahoo (`GC=F`) is rate-limited/ToS-restricted.
- **Not unavailable at every price** ⇒ §10 hard-stop does **not** fire.
- **Procure:** Nasdaq Data Link `LBMA/GOLD` (free API key) for the London fix,
  or a paid LBMA/ICE feed, or vendor gold-futures (GC) if the overnight session
  is wanted. First-public semantics to record on procurement: the **London PM
  fix is ~15:00 London time**; use the fix knowable at the decision cut.

### BTC / ETH — already in the PIT store
- Binance USDS-M perpetuals + funding in `xsmom.db` (Stage 1, frozen,
  survivorship-safe, `close_time`-stamped). 2,404 daily bars each,
  2020-01-01 → 2026-08-31. Funding carry is the §7.2 `M0` input. No procurement
  needed.

---

## What must be procured (summary)

| Need | Why | Candidate | Effort |
|---|---|---|---|
| **gold** daily | no free source reachable headless | Nasdaq Data Link `LBMA/GOLD` (free key) or paid LBMA/ICE | key signup or licence |
| **DXY** exact index | ICE proprietary; only a substitute is free | ICE/vendor licence, or adopt `DTWEXBGS` substitute | licence or a review decision |
| **ES/NQ** futures (overnight) | cash-index substitutes miss the Globex session | CME/vendor continuous futures | licence |

If the delegates accept the free substitutes (broad-dollar for DXY; cash
indices for ES/NQ) and a keyed/paid gold feed, **the entire M1 panel is
obtainable**; nothing is blocked at every price.

## Blockers encountered (evidence for the record)

- **Stooq** (`^spx`, `^ndx`, `^vix`, `es.f`, `nq.f`, `dx.f`, `gc.f`): serves a
  SHA-256 **JavaScript proof-of-work challenge** instead of CSV to non-browser
  clients — unusable in a headless PIT pipeline.
- **Yahoo Finance** chart API: `429 Too Many Requests` from this environment,
  and its terms restrict redistribution — not a dependable PIT source.
- **FRED** stalls (read-timeout) on library/browser User-Agents but serves a
  `curl` UA in ~0.6 s; the staging tool sets a curl UA accordingly
  (`tools/g3a_exogenous_download.py`).

## Files produced

- `data/exogenous/*.csv` — 7 raw series, staged, **not adopted**.
- `data/exogenous/MANIFEST.json` — per-series source, URL, role
  (primary/substitute), granularity, cost, first-public semantics, PIT-usable
  rule, SHA-256, coverage, `adopted:false`.
- `tools/g3a_exogenous_download.py` — the audit staging tool (download + audit
  only; fits nothing, touches neither `xsmom.db` nor the holdout).

## Next (per governance Order of work)

1. **Owner:** append §68.0–§68.10 to `NOTES.md`, dated, user decisions
   labelled. *(Not done here — canonical ledger is the owner's write.)*
2. Delegate review of substitutes vs. procurement for DXY / ES / NQ / gold;
   record adoption decisions.
3. **G3-B** cross-asset structure: freeze the lead/lag + rolling-beta protocol
   (window, `k` range, statistics) **in the ledger before reading**, then emit
   raw series with **no narrative labels** (§10 G3-B, §63.2 discipline).
4. **Stop** at the spec stage (feature lists ≤8, forecast form, `λ` + caps,
   calibration, `α`, exact criteria, lock commit) before the single G3-C trial.

**Do-not reminders honoured:** no breadth floor / maturity threshold introduced;
no LLM assigned any ancestry or taxonomy; no group prior computed; `M0`/`M1`
not fitted; no development returns read; G3-C not run; holdout untouched; no
narrative label attached to any series.
