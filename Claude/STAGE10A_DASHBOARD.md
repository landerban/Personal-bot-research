# Stage 10a — Paper-trading dashboard (read-only, local)

A small local web UI showing what the paper harness is doing, independent of
Binance's interface. **Strictly read-only**: it displays the bot's own records;
it can never place, modify, or cancel anything.

Runs alongside Stage 10. No trials, no strategy changes, holdout untouched.

---

## 0. Design principle — the UI is a second witness, not a control panel

The dashboard reads **the harness's own logs and state files**, not the
exchange. That is the point of "differently from Binance": it shows what *the
bot believes* — its equity series, its positions, its funding records. When the
dashboard and Binance's screen disagree, that disagreement is reconciliation
signal, which is exactly what Stage 10 exists to surface.

Consequences, non-negotiable:

- **The UI process holds no API keys.** Exchange-reported state (balance,
  positions as Binance sees them) is already fetched by the harness during
  reconcile; the harness writes it into a status snapshot, and the UI reads the
  snapshot. One process talks to the exchange; the UI talks to files.
- **No write endpoints of any kind.** No order buttons, no config editing, no
  "flatten" button. If a control surface is ever wanted, it is a separate
  decision with its own review — a dashboard that can trade is an attack
  surface and an accident surface.
- **Binds to 127.0.0.1 only** by default. If remote viewing is wanted, the
  answer is an SSH tunnel or Tailscale, not `0.0.0.0`.

## 1. Data source

Add to the harness (not the UI) a `status.json` written atomically
(temp-file + rename) at the end of every cycle and every reconcile, containing
the current snapshot; the UI also tails the existing JSONL logs for history:

- `status.json`: timestamp, equity (re-baselined paper series), exchange
  balance at last reconcile, positions (symbol, side, notional, entry, mark,
  uPnL, target weight, actual weight), gross leverage, realised beta, kill
  switch state + distance, watchdog heartbeat age, day counter toward 28,
  today's skips + reasons, shadow reconciliation result for today,
  testnet_reset events
- history from existing files: daily report log, costlog (`venue=testnet`),
  funding records, anomaly/error log

The UI never parses anything the harness doesn't already write — if a field is
missing, add it to the harness's report, not to a second data path.

## 2. The page — one screen, glanceable

Single page, auto-refresh every 30–60s, dark, readable on a phone. Sections in
priority order:

1. **Status strip** (always visible): one light — GREEN (running, all checks
   passing), AMBER (running, anomaly noted today), RED (stopped / kill switch /
   heartbeat stale > 2 cycles). Beside it: heartbeat age, day N of 28, kill
   switch distance (e.g. "DD 4.2% of 30%").
2. **Equity**: paper equity curve since paper start (re-baselined series;
   reset events marked), today's PnL, cumulative PnL split price vs funding.
3. **Book**: positions table — symbol, side, notional, entry → mark, uPnL,
   target vs actual weight (mismatch highlighted). Gross leverage and beta
   under it.
4. **The six §46 criteria** as a checklist with live status — shadow
   reconciliation streak, funding drift ($ cumulative vs exchange income),
   crash-recovery count, §4 fixes demonstrated (static once done), watchdog
   verified, silent-error count (must stay 0).
5. **Today's activity**: fills (from costlog), skips with reasons, funding
   settlements recorded.
6. **Anomaly feed**: tail of the error/anomaly log, newest first. Empty is the
   goal; nothing may be filtered out of it.

No charts beyond the equity line. No candlesticks, no prices, no market data —
Binance already does that; this page answers "is my machine healthy and honest,"
not "what is the market doing."

## 3. Implementation constraints

- One small FastAPI (or Flask) app + one HTML template + minimal vanilla JS
  for refresh. No build step, no framework, no database — it reads files.
- Read-only file access; opens logs with read flags only.
- Survives missing/partial files gracefully (harness mid-write, day one with
  no history): show "no data yet," never crash.
- A stale `status.json` (older than 2 cycle intervals) forces the status light
  RED with "harness not reporting" — the dashboard must fail loud when its
  source goes quiet, or it becomes a reassurance machine.
- Start with `python -m dashboard` from the repo; document the one command in
  the README.

## 4. Tests

- Renders correctly against: a healthy snapshot, a missing `status.json`, a
  stale snapshot, a snapshot with a MISMATCH day, and a testnet_reset day.
- Grep/static check: the UI module imports no exchange client and reads no env
  var containing `KEY` or `SECRET`.
- Atomic-write test on the harness side: a reader never sees a torn
  `status.json`.

## 5. Acceptance

- Dashboard runs locally with one command, binds 127.0.0.1
- Shows all six sections from harness-written files only
- No API keys, no exchange client, no write endpoints in the UI process
- RED on stale source; graceful on missing data
- §4 tests green; existing suites untouched and green
- No strategy code modified; budget unchanged; holdout sealed

## 6. Do not

- Give the UI keys, an exchange client, or any write/control endpoint
- Bind beyond localhost by default
- Add market data, price charts, or anything Binance's own screen already does
- Filter or summarise the anomaly feed
- Let a pretty dashboard substitute for the daily report — §7 of Stage 10
  still runs
