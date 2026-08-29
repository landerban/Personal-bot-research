"""
Stage 14 B.2.1: the supervisor -- one process the user starts and forgets.

Runs the dashboard continuously and the paper cycle once a day, writes a
heartbeat, restarts a crashed child with exponential backoff, holds the
single-instance lock, and shuts down cleanly.

TESTNET ONLY. The cycle goes through TestnetClient, which cannot be pointed at
a production venue. Nothing here creates a path to real money; that stays
gated on the holdout decision (NOTES 49.3).

EXIT CODES
  0  clean shutdown
  2  another instance is already running (the lock refused)
  3  unrecoverable CONFIG error -- missing keys, failed venue guard. These do
     not get retried: a supervisor that restart-loops on a bad config burns
     the machine and hides the message.
"""

from __future__ import annotations

import logging
import logging.handlers
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xsmom import schedule as S  # noqa: E402
from xsmom.lock import AlreadyRunning, SingleInstanceLock  # noqa: E402

log = logging.getLogger("xsmom.supervisor")

STATE_DIR = ROOT / "live" / "state"
LOCK_PATH = STATE_DIR / "supervisor.lock"
CLOCK_PATH = STATE_DIR / "clock.json"
STATUS_PATH = STATE_DIR / "status.json"
HEARTBEAT_PATH = STATE_DIR / "heartbeat"
# A stop SENTINEL rather than a signal. On Windows `taskkill` without /f
# only posts WM_CLOSE to GUI windows, so a console supervisor never sees
# it and gets force-killed instead -- skipping the clean-shutdown path
# entirely and leaving a stale lock. A file both sides agree on works the
# same way on every OS and needs no signal semantics.
STOP_FILE = STATE_DIR / "stop"

HEARTBEAT_S = 30.0
# How often the idle wait checks for the stop sentinel. The supervision
# tick stays at HEARTBEAT_S; this only makes shutdown prompt.
STOP_POLL_S = 1.0
BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 300.0        # ~5 minutes, per STAGE14 B.2.1


class ConfigError(RuntimeError):
    """Unrecoverable: do not retry, exit 3 and say why."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)sZ %(levelname)-7s %(name)s: %(message)s")
    fmt.converter = time.gmtime
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "xsmom.log", maxBytes=5_000_000, backupCount=7,
        encoding="utf-8")
    handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler, console]


def preflight() -> dict:
    """Fail fast and LOUDLY on anything that cannot be fixed by retrying.

    Checked here rather than at first use so a misconfigured install dies in
    the first second with a readable message, instead of at 00:00 UTC.
    """
    from live.client import TESTNET_HOSTS, TestnetClient, env_credentials

    key, secret = env_credentials()
    if not key or not secret:
        raise ConfigError(
            "no testnet API credentials in the environment. Expected "
            "BINANCE_TESTNET_KEY / BINANCE_TESTNET_SECRET (or the "
            "..._API_KEY / ..._API_SECRET aliases), sourced from a file "
            "OUTSIDE the repo -- see the runbook. The installer never writes "
            "keys anywhere.")
    try:
        client = TestnetClient()          # asserts the venue at construction
    except ValueError as e:
        raise ConfigError(f"venue guard refused the client: {e}") from None
    if client.base_url.split("/")[2] not in TESTNET_HOSTS:
        raise ConfigError(f"base_url is not a testnet host: {client.base_url}")
    return {"base_url": client.base_url, "key_len": len(key)}


class Supervisor:
    def __init__(self, dashboard_port: int = 8787, log_dir: Path | None = None,
                 clock=utcnow, run_dashboard: bool = True):
        self.port = dashboard_port
        self.log_dir = log_dir or (ROOT / "logs")
        self._clock = clock
        self.run_dashboard = run_dashboard
        self.stop = threading.Event()
        self.httpd = None
        self._dash_thread: threading.Thread | None = None
        self._dash_backoff = BACKOFF_START_S
        self.dashboard_restarts = 0
        self.cycles_run = 0

    # ---------------------------------------------------------- dashboard

    def _start_dashboard(self) -> None:
        from dashboard.server import serve
        self.httpd = serve(status_path=STATUS_PATH, host="127.0.0.1",
                           port=self.port)
        self._dash_thread = threading.Thread(
            target=self.httpd.serve_forever, name="dashboard", daemon=True)
        self._dash_thread.start()
        log.info("dashboard serving on http://127.0.0.1:%d", self.port)

    def _supervise_dashboard(self) -> None:
        """Restart a dead dashboard with exponential backoff to ~5 min. The
        dashboard dying must never take the trader down with it."""
        if not self.run_dashboard or self.stop.is_set():
            return
        if self._dash_thread is not None and self._dash_thread.is_alive():
            self._dash_backoff = BACKOFF_START_S      # healthy: reset backoff
            return
        log.error("dashboard thread is dead; restarting in %.0fs",
                  self._dash_backoff)
        if self.stop.wait(self._dash_backoff):
            return
        try:
            self._start_dashboard()
            self.dashboard_restarts += 1
        except Exception as e:
            log.exception("dashboard restart failed: %s", e)
            self._dash_backoff = min(self._dash_backoff * 2, BACKOFF_MAX_S)

    # -------------------------------------------------------------- cycle

    def run_cycle_now(self, kind: str, date: str) -> bool:
        """One paper cycle. Returns True if it completed.

        A cycle that RAISES is a crash and is reported as one; it does not
        silently count as a day.
        """
        from live.client import TestnetClient
        from live.costlog import CostLog
        from live.phase2 import Phase2Config, Phase2Runner, run_cycle
        from live.status import StatusSnapshot, write_status

        log.info("cycle start (%s) for %s", kind, date)
        client = TestnetClient()
        runner = Phase2Runner(client, Phase2Config())
        res = run_cycle(runner, costlog=CostLog(ROOT / "paper_costs.jsonl"),
                        execute=True, log_path=ROOT / "paper_log.jsonl")
        self.cycles_run += 1
        log.info("cycle done: %s | shadow=%s | positions=%d | errors=%d",
                 res.skip_reason or f"{len(res.decision or {})} names",
                 res.shadow.get("result"), len(res.filled), len(res.errors))

        clock = S.ClockState.load(CLOCK_PATH)
        clock.record_cycle(date, kind)
        clock.save(CLOCK_PATH)

        cfg = Phase2Config()
        write_status(STATUS_PATH, StatusSnapshot(
            ts=time.time(), cycle_interval_s=86_400.0,
            equity=cfg.capital, baseline_equity=cfg.capital,
            exchange_balance=res.equity,
            kill_switch_armed=True, kill_switch_threshold=cfg.kill_switch_dd,
            drawdown=0.0, heartbeat_age_s=0.0,
            day_counter=clock.day_counter, day_target=clock.day_target,
            skips=({res.skip_reason.split(":")[0]: 1} if res.skip_reason else {}),
            shadow=res.shadow,
            composition_guard={"alert": res.guard_alert,
                               "reason": res.guard_reason,
                               "excluded": res.excluded[:20], "ambiguous": [],
                               "excluded_in_top15": 0},
            anomalies=([{"ts": date, "msg": e} for e in res.errors]
                       + ([{"ts": date, "msg": f"{kind}: ran late but inside "
                            f"the 2h grace window; day counts (NOTES 51.4)"}]
                          if kind == S.LATE else [])),
        ))
        return True

    # --------------------------------------------------------------- loop

    def beat(self) -> None:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(f"{time.time():.3f}", encoding="utf-8")

    def tick(self) -> None:
        """One supervision step: heartbeat, dashboard health, cycle if due."""
        self.beat()
        self._supervise_dashboard()
        now = self._clock()
        clock = S.ClockState.load(CLOCK_PATH)
        should, kind, date = S.due_cycle(now, clock)
        if should:
            try:
                self.run_cycle_now(kind, date)
            except Exception:
                # A crashed cycle is NOT a completed day. It is logged loudly
                # and retried on the next tick only if still inside the grace
                # window -- never silently counted.
                log.exception("cycle FAILED for %s (%s)", date, kind)
        elif kind == S.MISSED and date not in clock.missed_days:
            # ONCE per missed date, not once per poll tick. The guard used to
            # be `last_cycle_date != date`, which stays true forever on a
            # missed day, so a single missed date produced a WARNING every 30
            # seconds -- 45 identical lines before this was fixed. A log that
            # repeats itself is a log nobody reads, and it would have buried
            # the next real event.
            #
            # `missed_days` is the durable record, so this stays quiet across
            # restarts too, not just across ticks.
            clock.record_missed(date)
            clock.save(CLOCK_PATH)
            log.warning(
                "MISSED cycle for %s -- host was off or asleep past the 2h "
                "grace window. Book held; the 28-day counter PAUSES at %d "
                "(it does not reset -- NOTES 51.4).",
                date, clock.day_counter)
            self._note_missed(date, clock.day_counter)

    MISSED_MARKER = "missed_cycle"

    def _note_missed(self, date: str, counter: int) -> None:
        """One anomaly-feed entry per missed date, so the dashboard can say
        WHY the counter is not advancing.

        Without this the feed received nothing at all on a missed day and the
        operator saw a stalled count with no explanation. Idempotent by
        construction: the existing feed is scanned for this date's marker
        first, so a restart cannot double it.
        """
        try:
            from live.status import read_status, write_status

            snap = read_status(STATUS_PATH) or {}
            feed = list(snap.get("anomalies") or [])
            for a in feed:
                if (isinstance(a, dict) and a.get("kind") == self.MISSED_MARKER
                        and a.get("ts") == date):
                    return                      # already noted for this date
            feed.append({
                "kind": self.MISSED_MARKER, "ts": date,
                "msg": (f"missed_cycle {date}: host off or asleep past the 2h "
                        f"grace window. Book held; 28-day counter paused at "
                        f"{counter} (not reset -- NOTES 51.4)."),
            })
            snap["anomalies"] = feed
            snap["ts"] = time.time()
            write_status(STATUS_PATH, snap)
        except Exception:
            log.exception("could not record the missed-cycle anomaly for %s",
                          date)

    def run(self) -> int:
        lock = SingleInstanceLock(LOCK_PATH)
        try:
            info = lock.acquire()
        except AlreadyRunning as e:
            log.error("%s", e)
            return 2
        if info.reclaimed_from:
            log.warning("reclaimed a stale lock from dead pid %d "
                        "(previous run did not shut down cleanly); recovery "
                        "goes through reconcile on the next cycle",
                        info.reclaimed_from)
        try:
            cfg = preflight()
        except ConfigError as e:
            log.error("CONFIG ERROR (not retrying): %s", e)
            lock.release()
            return 3
        log.info("preflight ok: venue %s, credentials present", cfg["base_url"])

        STOP_FILE.unlink(missing_ok=True)   # never inherit a stale request
        self._clear_halted()
        self._install_signal_handlers()
        try:
            if self.run_dashboard:
                self._start_dashboard()
            log.info("supervisor up (pid %d). next cycle %s",
                     info.pid,
                     S.next_cycle_after(self._clock()).strftime("%Y-%m-%d %H:%M:%SZ"))
            while not self.stop.is_set():
                self.tick()
                self._wait_interruptibly(HEARTBEAT_S)
        finally:
            log.info("shutting down")
            if self.httpd is not None:
                try:
                    self.httpd.shutdown()
                    self.httpd.server_close()
                except Exception:
                    pass
            self._final_status()
            lock.release()
            log.info("stopped cleanly; lock released")
        return 0

    def _wait_interruptibly(self, seconds: float) -> None:
        """Idle between ticks, but notice a stop request within a second."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stop.wait(min(STOP_POLL_S, deadline - time.monotonic())):
                return
            if self._stop_requested():
                return

    def _stop_requested(self) -> bool:
        """True if stop_bot.bat (or anything else) asked us to shut down."""
        try:
            if STOP_FILE.exists():
                STOP_FILE.unlink(missing_ok=True)
                log.info("stop requested via %s", STOP_FILE.name)
                self.stop.set()
                return True
        except OSError:
            pass
        return False

    def _clear_halted(self) -> None:
        """Clear the shutdown flag left by the previous run.

        `_final_status` sets halted=True so a stopped bot reads as stopped.
        Nothing cleared it on the way back up, so after one clean stop/start
        the dashboard showed RED "supervisor stopped" forever while the bot
        was in fact running -- a status line that lies in the reassuring
        direction is bad, but one that cries wolf gets the whole light
        ignored.
        """
        try:
            from live.status import read_status, write_status

            snap = read_status(STATUS_PATH)
            if snap and snap.get("halted"):
                snap["halted"] = False
                snap["halt_reason"] = None
                snap["ts"] = time.time()
                write_status(STATUS_PATH, snap)
                log.info("cleared the halted flag from the previous shutdown")
        except Exception:
            log.exception("could not clear the halted flag")

    def _final_status(self) -> None:
        """STAGE14 B.2.6: leave a truthful last word rather than a stale file
        that looks alive."""
        try:
            from live.status import read_status, write_status
            snap = read_status(STATUS_PATH) or {}
            snap["ts"] = time.time()
            snap["halted"] = True
            snap["halt_reason"] = "supervisor stopped (clean shutdown)"
            write_status(STATUS_PATH, snap)
        except Exception:
            log.exception("could not write the final status snapshot")

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):
            log.info("signal %s received; stopping", signum)
            self.stop.set()

        for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass          # not the main thread, or unsupported here


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="xsmom")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--log-dir", default=str(ROOT / "logs"))
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="run a single supervision tick and exit (install "
                         "verification; does not hold the loop open)")
    a = ap.parse_args(argv)

    setup_logging(Path(a.log_dir))
    sup = Supervisor(dashboard_port=a.port, log_dir=Path(a.log_dir),
                     run_dashboard=not a.no_dashboard)
    if a.once:
        try:
            preflight()
        except ConfigError as e:
            log.error("CONFIG ERROR: %s", e)
            return 3
        sup.tick()
        return 0
    return sup.run()


if __name__ == "__main__":
    raise SystemExit(main())
