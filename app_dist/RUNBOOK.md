# xsmom paper bot — runbook

**Testnet only. No real money. Not a path to real money.**
This runs the frozen research config against the Binance *futures testnet*
with play funds, to prove the machine works. Its profit and loss mean nothing
and count toward nothing.

---

## The two rules of thumb

1. **Machine plugged in.**
2. **Sleep disabled.**

A sleeping machine at 00:00 UTC is the single most common way a local 24/7 bot
quietly stops working. Set it once, on AC power:

```
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

---

## Start it

Double-click **`start_bot.bat`**.

A console window opens and stays open — that window *is* the bot. Closing it
stops the bot cleanly. Minimise it if it is in the way.

First time only: double-click **`install.bat`** first.

## Know it is alive

**The dashboard: <http://127.0.0.1:8787>**

One light tells you everything:

| Light | Meaning | What to do |
|---|---|---|
| **GREEN** | running, all checks passing | nothing |
| **AMBER** | running, but something wants a human eye — an anomaly today, a composition-guard alert, a stale metadata snapshot, or a shadow result that is not a clean match | read the page; it says which |
| **RED** | **stopped, or not reporting** — the harness is dead, the status file is stale (>2 days), the kill switch fired, or shadow reconciliation MISMATCHED | see *When it goes RED* |

The dashboard reads only the bot's own files. It holds no API keys, has no
exchange client, and has no buttons that can trade — it is a witness, not a
control panel. If it disagrees with Binance's own screen, that disagreement is
the signal.

**The other glance:** Task Scheduler → task **`xsmom-paper-bot`** → *Last Run
Result* should be `0x0` or "running".

## Stop it

Close the console window, or double-click **`stop_bot.bat`**.

Stopping is safe. The supervisor finishes or safely abandons the in-flight
step, writes a final status file, and releases its lock. On the next start it
reconciles against the exchange before doing anything — the exchange is always
the source of truth.

**Stopping does not reset the 28-day clock.** See *The clock* below.

## Update it

```
cd C:\Stock
git pull
```

then restart the bot. Restarts are safe and do not reset the clock.

## Where things live

| | |
|---|---|
| Bot logs (rotating, 7 files) | `logs\xsmom.log` |
| What the bot believes right now | `C:\Stock\live\state\status.json` |
| Day counter & clock history | `C:\Stock\live\state\clock.json` |
| Single-instance lock | `C:\Stock\live\state\supervisor.lock` |
| Every cycle, one line each | `C:\Stock\paper_log.jsonl` |
| Every fill and funding row | `C:\Stock\paper_costs.jsonl` |
| Code | `C:\Stock` |

## Keys

Keys live in **`%USERPROFILE%\.binance_testnet.env`** — deliberately *outside*
the code folder, so they can never be committed. The installer never writes
them. Format:

```
BINANCE_TESTNET_KEY=...
BINANCE_TESTNET_SECRET=...
```

If that file is missing the bot exits immediately with code **3** and says so.
It does not retry — a bot that restart-loops on a bad config hides the message.

**These are testnet keys and should be treated as already disclosed** (they
passed through a chat transcript). Rotate them when the paper phase ends.

## The clock

The phase needs **28 days**. What each day does to the count was fixed in
advance so it can never be argued about afterwards:

| What happened | Day counts? | Counter |
|---|---|---|
| Cycle ran on time | yes | +1 |
| Cycle ran **late** but within 2 hours of 00:00 UTC | yes | +1 |
| Machine was **off or asleep** past that window | no | **pauses** — no change |
| **Unrecovered crash**, or an **unexplained shadow mismatch** | no | **resets to 0** |

A machine that was switched off is not a failure of the machine under test, so
it neither credits nor destroys the count. A crash *is* evidence, and resets.

A **skipped trading day is not a missed day.** The strategy legitimately skips
~1 day in 5 at this size, because it cannot seat a large enough position in
BTC. A skip still counts as a completed cycle.

## When it goes RED

1. **Look at the dashboard banner** — it names the reason.
2. **`logs\xsmom.log`** — the last few lines say what happened.
3. Common ones:

| Reason | Meaning | Action |
|---|---|---|
| `no status.json` / `stale` | the bot is not running | start it |
| `CONFIG ERROR`, exit 3 | keys missing or unreadable | fix the env file |
| `ALREADY RUNNING`, exit 2 | a second copy was refused — correct behaviour | use the running one |
| shadow **MISMATCH** | the live path and the research code disagreed on the book | **stop and investigate** — do not restart into it |
| kill switch | 30% drawdown reached | investigate before restarting |

**Never delete `supervisor.lock` to "fix" a refusal** unless you are certain no
bot is running. That lock is what stops two copies placing the same orders
twice.

## Stronger auto-start (optional, needs an admin shell)

`install.bat` registers a start-at-logon task without needing admin rights.
For start-at-boot and automatic restart-on-failure, run this in an
**elevated** PowerShell:

```powershell
$app = "$env:USERPROFILE\Desktop\App"
schtasks /create /tn "xsmom-paper-bot" /sc onstart /ru "$env:USERNAME" /rl highest `
  /tr "`"$app\start_bot.bat`"" /f
schtasks /change /tn "xsmom-paper-bot" /ri 10 /du 9999:59
```

## What this bot will never do

- Trade real money. There is no production endpoint anywhere in the code, and
  the client refuses any host that is not a known testnet host.
- Change the strategy based on how paper trading goes.
- Let two copies run at once.
- Count testnet profit toward anything.
