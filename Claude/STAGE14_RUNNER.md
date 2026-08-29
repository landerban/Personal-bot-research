# Stage 14 — Production snapshot wiring + the standalone 24/7 runner

Two parts. **Part A** wires the user-supplied production `exchangeInfo`
snapshot into the crypto filter, closing §48.14.1. **Part B** turns the paper
harness into a standalone app the user launches once on their local machine
and forgets — outside VS Code, surviving reboots and crashes.

§0 of `STAGE2_PROMPT.md` remains in force. **No trials.** No strategy changes.
The $800 paper clock continues; holdout sealed.

---

# PART A — Wire the production snapshot (§48.14.1 closure)

## A.1 Ingest

The user has committed a production `fapi exchangeInfo` JSON to the repo.
Locate it, record its path and fetch date in NOTES §51, and load it as a
**second metadata source** alongside the testnet snapshot:

- Production snapshot: authoritative for classification (it sees every listed
  instrument, closing the §48.11 blindness)
- Testnet snapshot: still used for testnet-tradeability (what the paper book
  can actually hold)
- The no-mainnet-host rule is intact — the file arrived out-of-band; assert no
  mainnet URL entered the codebase.

## A.2 Verify the filter against full production reality

1. Classify **every** production instrument. Report counts: crypto-eligible /
   TradFi-excluded / ambiguous. The §48.11 seven (BZ, DRAM, EWY, SAMSUNG,
   MRVL, AMD, NBIS) must now classify by **metadata**, not by the recency
   guard's ambiguity fallback — confirm each, and confirm the seeded-list
   five (SNDK, SKHYNIX, MU, SOXL, CL) are also caught by metadata now.
2. Report any instrument where testnet and production metadata **disagree** on
   classification — that is exactly the §48.10 hazard, enumerated at last.
3. Re-run the no-op proof (Test 26) — must remain **zero diffs**; the snapshot
   changes classification coverage, never history.
4. The recency guard (`suspicious_absences`) now runs against production
   metadata; the `underlying_ambiguous` population should shrink toward
   genuinely new listings. Report before/after counts.
5. **Staleness rule, pre-registered:** the snapshot ages. When it is older
   than 30 days, the dashboard composition line goes AMBER with
   "metadata snapshot stale — refresh advised." Refreshing is always a
   user-supplied file, never an in-code fetch.

# PART B — The standalone runner

## B.1 What "app" means here — requirements, not decoration

The deliverable is: **one thing the user double-clicks (or that starts itself
at boot), which runs the bot cycle daily and the dashboard continuously, with
no VS Code, no terminal babysitting.** Detect the host OS and target it — the
§49.6 Windows atomicity bug says this machine is Windows; build Windows-first,
with the POSIX equivalent documented if trivial.

Prefer boring machinery: a venv + launcher script + OS scheduler
(Task Scheduler on Windows). **No PyInstaller/exe packaging by default** —
single-file exes invite antivirus false positives and build fragility for zero
functional gain here; note it as an option only if the user later asks.

## B.2 Components

1. **`run.py` / `python -m xsmom`** — the supervisor entrypoint: starts the
   dashboard server and the cycle scheduler in one process tree, writes a
   heartbeat, restarts a crashed child with exponential backoff (max ~5 min),
   and exits nonzero only on unrecoverable config errors (missing keys, bad
   URL guard).
2. **`install` step** (script, run once): creates the venv, installs deps,
   registers the OS scheduled task — *at logon + at boot, restart on failure* —
   pointing at the launcher; prints (does not silently change) the power
   settings the user must set: **disable sleep/hibernate while plugged in**,
   since a sleeping machine at cycle time is the #1 real-world killer of a
   24/7 local bot. Clock sync (NTP) verified and reported.
3. **`start_bot` launcher** (`.bat`/shortcut on Windows): activates venv, runs
   the supervisor, logs stdout/stderr to a rotating file.
4. **Single-instance lock.** A lock file with PID + liveness check: a second
   launch must refuse loudly, not create a twin trader double-placing orders.
   This is the highest-risk failure of "just double-click it" and gets its own
   test.
5. **Secrets**: keys load from a `.env`-style file *outside* the repo tree (or
   user env vars), path documented; `scan_secrets` still clean; the installer
   never writes keys anywhere.
6. **Clean shutdown**: SIGTERM/console-close handled — finish or abort the
   in-flight step safely, write a final `status.json`, release the lock.
   Recovery on next start goes through reconcile (the demo-3 path), which is
   already the tested behaviour.

## B.3 The missed-cycle policy — pre-register in NOTES §51 before building

A local machine will sometimes be off or asleep at 00:00 UTC (09:00 KST). Fix
the policy now, not in the moment:

- Woken/started within a **2-hour grace window** of the scheduled cycle → run
  a **late cycle**, marked `late_cycle` in the daily report; decisions use the
  same close-gated inputs (unchanged), fills happen at the later time and the
  fill-divergence measurement simply records it.
- Beyond the grace window → log `missed_cycle`, hold the book, next regular
  cycle proceeds normally.
- **Clock accounting, fixed now:** a `missed_cycle` day **pauses** the 28-day
  counter (the day doesn't count, the count doesn't reset) when the cause is
  the host being off/asleep; it **resets** only under the existing §46 rules
  (unrecovered crash, unexplained mismatch). A `late_cycle` day counts
  normally. Record this interpretation so the eventual 28-day verdict isn't
  argued retroactively.

## B.4 Tests

- Single-instance: second launch refuses; stale lock from a dead PID is
  reclaimed.
- Supervisor restarts a deliberately-crashed child with backoff; gives up
  correctly on a config error.
- Missed/late cycle paths: simulate by clock injection, assert the
  `late_cycle` / `missed_cycle` markers and counter behaviour.
- Shutdown mid-cycle → restart → reconcile leaves a correct book (extends the
  existing demo-3 machinery).
- Installer is idempotent (run twice, one task registered).

## B.5 Runbook — one page, in the repo

For the user: how to start it, how to know it's alive (dashboard + one
Task Scheduler glance), how to stop it, how to update (git pull → restart —
restarts are safe and do not reset the clock), where the logs live, what the
three status lights mean, and the two rules of thumb: *machine plugged in,
sleep disabled.*

## Order of work

1. NOTES §51: snapshot provenance, staleness rule, missed-cycle policy —
   dated, before code
2. Part A verification; Test 26 re-run; report the classification deltas
3. Part B build; §B.4 tests green alongside all existing suites
4. Install on the user's machine; confirm one full unattended cycle (or
   late/skip handling) with the dashboard reachable and VS Code closed
5. Report. Clock continues under §46 + the §B.3 accounting.

## Acceptance

- §51 pre-registered before code
- Production classification counts; the seven absences metadata-classified;
  testnet/production disagreements enumerated; Test 26 zero diffs
- Staleness AMBER wired
- One-command install; auto-start at boot; auto-restart with backoff;
  single-instance lock tested
- Missed/late cycle policy implemented exactly as §B.3 registered
- Keys outside the repo; scan clean
- Runbook present; one unattended cycle observed with VS Code closed
- Budget **15 of 25**; clock accounting per §B.3; holdout sealed

## Do not

- Fetch exchangeInfo from any host in code — snapshots are user-supplied files
- Package as a single exe by default
- Let two instances trade (lock is mandatory, tested)
- Decide missed-cycle clock treatment after the fact
- Change any strategy parameter; touch mainnet or the holdout
