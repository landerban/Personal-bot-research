# Stage 15 — Unblock the paper phase, wire the alarm, arm the measurements

One stage, four jobs in dependency order: **(A)** the real-volume shortlist fix
that lets the paper book form at all, **(B)** push alerting so the safety layer
reaches a human, **(C)** the no-hardcoded-state test that makes the §52 fault
class impossible, **(D)** measurement instrumentation — shadow-maker, vol
diagnosis, regime context — wired ready so it collects the moment books exist.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials. No strategy parameter
changes.** Holdout sealed. Budget stays **15 of 25**.

---

# PART A — Real-volume shortlist (§53.4 option 1)

## A.1 Pre-register first, in NOTES §55

Before code: record that the live shortlist will rank by **production median
quote volume** (from the research store, currently through 2026-07-31),
intersected with what testnet lists — replacing testnet's synthetic 24h volume.

The grounds, stated: §48.1's universe rule says *median quote volume*. Testnet
synthetic volume **is not that quantity**; the store's production volume is.
This is a **more faithful implementation of the existing rule**, not a rule
change — §53.2 showed synthetic volume ranks junk (`我踏马来了USDT`) above
real majors, starving beta identification and blocking book formation on 12 of
12 replay days. The hedge guard was right to refuse; the input was wrong.

Also record: **the 28-day counter restarts at this fix** (it sits at ~day 1, so
the cost is nothing now, versus a graded-mixture argument at day 28). Days run
under the starved shortlist were plumbing exercise, not phase evidence.

## A.2 Implementation

1. Shortlist = top-40 by production median quote volume (store, PIT rule as in
   research) ∩ testnet-listed ∩ crypto-filter — then the normal pipeline.
2. **Staleness handling**: the store's volumes end 2026-07-31 and will age.
   Rule: volumes older than 60 days put the composition line AMBER
   ("volume reference stale — refresh store"); refresh is a user-run
   `pitdata/download` update, never an in-cycle fetch of anything beyond the
   feeds the cycle already uses.
3. The funding-presence filter keeps using the live 14-day funding fetch for
   *today's* candidacy (sufficient); note in code why replays older than that
   window are not supported (§53.1's withdrawn artifact).
4. **Verification before the counter restarts**: replay the last ~12 days
   through the corrected path. Report book-formation rate, skip reasons, and
   the beta table for the new shortlist (majors should dominate; SEs should
   look like §53.2's top block, not its bottom). If formation is still ~0%,
   stop and report — do not stack further guesses.

# PART B — Push alerting

## B.1 Channel

Telegram bot (cheap, reliable, phone-native). Token and chat id live with the
other secrets **outside the repo**; `scan_secrets` stays clean. If the user
prefers another channel, the sender is one small module behind an interface —
but build Telegram by default and document the 5-minute BotFather setup in the
runbook.

## B.2 What alerts — and what must not

Alert on state *transitions*, never on states (the §51.10-spam lesson, already
paid for once):

| Event | Level |
|---|---|
| Kill switch fired / `flatten_all` | CRITICAL |
| Watchdog: supervisor wedged / heartbeat stale | CRITICAL |
| Cycle error (incl. funding-reconcile drift beyond tolerance) | ALERT |
| Shadow-reconcile MISMATCH | ALERT |
| Dashboard-RED condition of any other kind | ALERT |
| Reset detected, `late_cycle`, composition/staleness AMBER | INFO (daily digest, not push) |
| Daily report line (one message: date, traded/skip, equity, day N of 28) | INFO push, once per day |

Each CRITICAL/ALERT fires **once per condition instance** with a cooldown;
recovery sends a single "resolved" message. A dead Telegram API must never
block or crash a cycle — send failures log locally and retry with backoff.

## B.3 Test

Simulated kill-switch fire, wedge, mismatch and recovery each produce exactly
one message (captured via a fake sender); a sender exception leaves the cycle
unharmed; no message contains keys or balances beyond the paper equity number.

# PART C — The no-hardcoded-state test

The §51.10 `halted` flag and the §52.1 `kill_switch_armed: True` literal are
the same fault: **reported state not derived from measured state.** Make the
third occurrence impossible:

- A test that drives the system through simulated distinct states (armed vs
  fired; drawdown 0 vs positive; halted vs running; heartbeat fresh vs stale)
  and asserts every safety-relevant `status.json` field **changes
  accordingly**. Any field constant across states that should distinguish them
  fails the test.
- Maintain the list of safety-relevant fields in one place next to the test so
  new fields get enrolled by default.

# PART D — Measurement instrumentation (ready-but-idle)

None of these act on anything. They collect.

## D.1 Shadow-maker (feeds the fill-probability model)

For every order the cycle actually places (taker), also log the counterfactual:
the post-only price that *would* have been quoted, and — from the bars/tape
already fetched — whether that price traded through within the cycle's
execution window. Fields: symbol, side, decision price, would-be maker price,
filled-through (bool), time-to-touch, taker fill price, `venue=testnet`.

This is logging only. **No maker orders are placed.** The Stage 2e rule stands:
no maker-mode result is reportable until a fill-probability model exists —
this builds the dataset that model will need, on testnet first, on real fills
later through the same pipeline.

## D.2 Vol-shortfall diagnosis (free, train, no adoption)

Decompose the persistent ~0.6–1.1pt realised-vol shortfall on the frozen
config: attribute between (a) the 60-day estimation window's lag, (b) beta-
shrinkage side-effects, (c) drop/renormalise interactions, (d) anything else —
by re-running the existing train run with instrumented sizing internals (a
diagnostic, `diagnostics.jsonl`, no trial). Report the attribution. **Adopt
nothing** — any estimator change alters position sizes and waits for the
post-holdout era with its own pre-registration.

## D.3 Regime context lines

Daily, from the live feed the cycle already pulls: cross-sectional dispersion
of 14d returns across the eligible universe, and mean pairwise correlation to
BTC. Two lines on the dashboard, appended to a small history file. Context and
accumulation only — no thresholds, no filters, no actions.

---

## Order of work

1. NOTES §55: A.1 pre-registration + counter restart + B.2 alert policy +
   the D-section "collect-only" scope — dated, before code
2. Part A; §A.2.4 replay verification; **stop and report if formation ~0%**
3. Part B with §B.3 tests; Part C test
4. Part D instrumentation; confirm D.1's report lands in diagnostics
5. Full suites green; counter restarted at the first post-fix cycle
6. Report: formation rate before/after, beta table, alert test evidence,
   D.1 attribution. Holdout sealed.

## Acceptance

- §55 pre-registered before implementation
- Shortlist ranks by production volume; staleness AMBER wired; replay shows
  books forming with identifiable betas — or the stage stopped and said so
- Counter restarted; the restart recorded with grounds
- Alerts fire on transitions only, once, with recovery messages; sender
  failure cannot harm a cycle; no secrets in messages
- No-hardcoded-state test enrolled and green
- Shadow-maker rows appearing once books form; zero maker orders placed
- D.1 attribution reported; nothing adopted
- Dashboard shows regime lines
- Budget **15 of 25**; no strategy parameter touched; holdout sealed

## Do not

- Place a maker order anywhere
- Adopt any vol-estimator change
- Alert on states instead of transitions
- Fetch volumes in-cycle from any new source — the store is the reference
- Count pre-fix days toward the 28
- Touch mainnet or the holdout
