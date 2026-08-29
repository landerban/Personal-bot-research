# Stage 12 — Start the live paper run (+ verify/build the dashboard)

Two jobs, in order: **(A)** check whether the Stage 10a dashboard was actually
built — evidence suggests it was not — and build it if missing; **(B)** start
the frozen config running live against testnet and open the 28-day clock.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials.** No strategy
parameters change. Holdout sealed throughout.

---

# PART A — The dashboard: verify, then build if absent

## A.1 Verify first, honestly

Check the repo for the Stage 10a deliverable:

1. Does a `dashboard/` module (or equivalent) exist with a serving entrypoint?
2. Does the harness write `status.json` atomically anywhere?
3. Do the Stage 10a §4 tests exist and pass (healthy / missing / stale /
   MISMATCH / reset snapshots)?
4. Can `python -m dashboard` (or the documented command) actually serve a page
   right now?

Report the answer plainly — **built / partially built / absent** — with file
paths as evidence. If it exists and works, say so and skip to Part B; do not
rebuild working code.

## A.2 If absent or partial: build to the Stage 10a spec

`STAGE10A_DASHBOARD.md` is the authority; do not redesign it. The
non-negotiables, restated:

- **Read-only.** No exchange client, no API keys, no env var containing
  `KEY`/`SECRET` readable by the UI process, no write/control endpoints.
- **Source = harness files only**: `status.json` (atomic temp-file + rename,
  written by the harness each cycle and each reconcile) plus the existing
  JSONL logs (daily report, costlog `venue=testnet`, funding, anomalies).
- **Binds 127.0.0.1** only.
- One page, auto-refresh 30–60s, phone-readable, dark:
  status light (GREEN/AMBER/RED) + heartbeat age + day N of 28 + kill-switch
  distance → equity curve (re-baselined, resets marked) → positions with
  target-vs-actual weights → the six §46 criteria as a live checklist →
  today's fills/skips/funding → unfiltered anomaly tail.
- **Stale `status.json` (> 2 cycle intervals) forces RED** — the dashboard
  must fail loud when its source goes quiet.
- One new status element beyond the original spec: the §48.6 **composition
  guard** line — today's excluded symbols count and any
  `underlying_ambiguous` events, AMBER on alert. The guard exists now; the
  dashboard should surface it.

Tests per Stage 10a §4, including the no-keys static check and the
torn-write test. All suites green before Part B starts.

---

# PART B — Start the paper run

## B.1 Preconditions — assert, do not assume

Before the first cycle, verify and report each:

| Check | Expected |
|---|---|
| §46 criteria recorded | NOTES §46, dated, before day one |
| Keys | env vars present; nothing key-like in repo (grep) |
| URL guard | testnet flag ↔ testnet host assertion fires both ways |
| Universe filter | crypto-only active; Test 26 green; composition guard wired |
| Production `exchangeInfo` snapshot | committed if the user supplied it (§48.14.1); if absent, log the gap as a standing limitation and proceed on testnet metadata + seeded list |
| Testnet coverage | 15/15 per §48.13; re-check on start day — testnet listings move |
| Config | frozen: top-15 crypto majors, lb14/skip0, N=10, k=5, vol 10%, $800 paper, b=0, kill switch 30% ARMED |
| Dashboard | serving, GREEN/RED behaviour verified against a stale file |
| Suites | all green (76/76 + dashboard tests) |

Any failure: stop and report. Do not start the clock on a broken precondition.

## B.2 The run

- Daily cycle at 00:00 UTC + settle wait, per the live spec: fetch → decide →
  execute → reconcile → shadow-reconcile → report → `status.json`.
- **Shadow reconciliation from day one** (Stage 10 §3): backtester re-decides
  on the same inputs; weights match to 1e-6 or it is a same-day
  stop-and-diagnose.
- Costlog rows tagged `venue=testnet` from the first fill.
- Watchdog armed; heartbeat visible on the dashboard.
- **Day counter starts at the first cycle that completes with all §46
  instrumentation live.** A cycle run before the dashboard/shadow/costlog were
  in place does not count toward the 28.

## B.3 The induced-failure schedule (Stage 10 §4/§8.3 — plan it, don't wing it)

The four Phase-2 fixes need demonstrations under real conditions. Schedule
them inside the first two weeks, one at a time, each on a day the previous
day's report was clean:

1. Undersized/rejected leg → atomicity repair fires (log the residual beta
   before/after)
2. Tight stop that fills → cascade reconcile re-hedges
3. Kill the process mid-cycle → restart reconciles to a correct book,
   no manual repair
4. Simulated ambiguous POST response → query-by-clientOrderId path runs before
   any resubmit

Each demonstration is a dated entry with evidence. **These do not reset the
28-day clock** — they are criterion 4 being satisfied, not failures. Only an
*unexplained* shadow mismatch or an *unrecovered* crash resets it (§46).

## B.4 Reporting rhythm

- Daily: the §46.7-format block, appended; dashboard reflects it.
- Weekly: equity curve, funding reconciliation vs exchange income history
  (cumulative, target ≤ $0.01 drift), composition-guard summary, anomaly
  review — a short written note, not just numbers.
- Any RED day: same-day diagnosis note, even if the fix is one line.

## B.5 What the run must never do

- Modify any strategy parameter in response to paper behaviour
- Count testnet PnL toward any success criterion
- Let a testnet balance reset fire the kill switch (§46.5 re-baseline rule)
- Read, fetch, or touch 2025+ historical return data — the live feed is
  today's data and is fine; the **holdout files stay sealed**
- Place anything on mainnet

## B.6 End state of this stage

The stage is "done" when: Part A verdict reported (and built if needed), all
B.1 preconditions green, and **day 1 of 28 has completed with a clean daily
report visible on the dashboard.** The 28-day clock then runs under Stage 10's
rules; subsequent check-ins report against the six §46 criteria.

## Acceptance

- Part A verdict with evidence; dashboard serving per spec (built or
  pre-existing), including the composition-guard line
- No keys/exchange client in the UI process (static check in tests)
- All B.1 preconditions asserted and reported
- Shadow reconciliation, costlog tagging, watchdog, dashboard live from day 1
- Induced-failure schedule written down with target dates
- Day 1 of 28 complete and clean
- Budget **15 of 25** unchanged; holdout sealed; no 2025+ historical data
  touched

## Do not

- Rebuild a working dashboard, or redesign it while building a missing one
- Start the 28-day clock before all instrumentation is live
- Treat induced failures as clock resets, or real unexplained failures as
  anything but resets
- Tune the strategy on paper behaviour
- Touch mainnet or the holdout
