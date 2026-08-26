#!/usr/bin/env python3
"""
Watchdog: a separate process with a separate lifecycle.

  python live/watchdog.py [--heartbeat live/state/heartbeat] [--stale 120]

The trader writes a heartbeat every 30 s. If it goes stale past 120 s the
watchdog flattens the account via live/killswitch.py and records the
trigger in paper_log.jsonl. It then keeps watching: if a restarted trader
resumes heartbeats, it simply resumes watching.

It shares NO code path with the trader — no client object, no config
loader, no imports from live/client.py or live/trader.py. The failure being
defended against is a trader that is alive but wedged; shared code means a
shared wedge.

Layering reminder (B3): this is layer 2. Layer 1 is the exchange-side
reduce-only stop placed at entry, which survives this machine dying. Layer 3
is the kill switch from your phone.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live.killswitch import flatten_all  # noqa: E402  (stdlib-only module)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HEARTBEAT = ROOT / "live" / "state" / "heartbeat"
DEFAULT_PAPER_LOG = ROOT / "paper_log.jsonl"


def heartbeat_age_s(path: Path, now: float | None = None) -> float | None:
    """Seconds since the trader last wrote the heartbeat; None if absent.
    Uses the timestamp INSIDE the file, not mtime, so a wedged process that
    somehow keeps touching the file without running its loop still trips."""
    try:
        ts = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return (now if now is not None else time.time()) - ts


def check_once(heartbeat: Path, stale_s: float, paper_log: Path,
               flatten=flatten_all, now: float | None = None) -> dict | None:
    """One watchdog tick. Returns the trigger record if it fired."""
    age = heartbeat_age_s(heartbeat, now)
    if age is not None and age <= stale_s:
        return None
    result = flatten()
    rec = {
        "kind": "watchdog_trigger",
        "ts": int((now if now is not None else time.time()) * 1000),
        "heartbeat_age_s": age,
        "flatten": result,
    }
    paper_log.parent.mkdir(parents=True, exist_ok=True)
    with open(paper_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--heartbeat", default=str(DEFAULT_HEARTBEAT))
    p.add_argument("--stale", type=float, default=120.0)
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--paper-log", default=str(DEFAULT_PAPER_LOG))
    p.add_argument("--grace", type=float, default=180.0,
                   help="seconds to wait for a first heartbeat before acting")
    a = p.parse_args()
    hb, plog = Path(a.heartbeat), Path(a.paper_log)
    if not (os.environ.get("BINANCE_TESTNET_API_KEY") and os.environ.get("BINANCE_TESTNET_API_SECRET")):
        sys.exit("watchdog: testnet API keys not in environment; refusing to start blind")

    start = time.time()
    fired_for_this_outage = False
    print(f"watchdog: heartbeat={hb} stale>{a.stale}s every {a.interval}s", flush=True)
    while True:
        age = heartbeat_age_s(hb)
        if age is None and time.time() - start < a.grace:
            time.sleep(a.interval)
            continue
        if age is not None and age <= a.stale:
            fired_for_this_outage = False
        elif not fired_for_this_outage:
            rec = check_once(hb, a.stale, plog)
            print(f"watchdog: TRIGGERED age={age} -> {json.dumps(rec['flatten'])}",
                  flush=True)
            fired_for_this_outage = True  # flatten once per outage, re-arm on recovery
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
