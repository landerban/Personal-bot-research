"""
Stage 10a §4 / Stage 12 Part A: dashboard tests.

Renders against a healthy snapshot, a missing one, a stale one, a MISMATCH
day and a testnet_reset day; proves the UI process holds no keys and no
exchange client; and proves a reader never sees a torn status.json.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.render import AMBER, GREEN, RED, render_page, status_light  # noqa: E402
from dashboard.server import DashboardHandler, serve  # noqa: E402
from live.status import (  # noqa: E402
    StatusSnapshot, is_stale, read_status, write_status,
)

NOW = 1_800_000_000.0
DAY = 86_400.0


def healthy(**over) -> dict:
    s = StatusSnapshot(
        ts=NOW, cycle_interval_s=DAY, equity=812.44, baseline_equity=800.0,
        exchange_balance=812.51, day_pnl=3.10, cum_price_pnl=10.2,
        cum_funding_pnl=2.24,
        equity_curve=[[NOW - 2 * DAY, 800.0], [NOW - DAY, 809.3], [NOW, 812.44]],
        positions=[{"symbol": "BTCUSDT", "side": "LONG", "units": 0.001,
                    "notional": 41.2, "entry": 41000.0, "mark": 41200.0,
                    "upnl": 0.2, "target_weight": 0.0515,
                    "actual_weight": 0.0515}],
        gross_leverage=0.24, realised_beta=0.011, kill_switch_armed=True,
        kill_switch_threshold=0.30, drawdown=0.012, heartbeat_age_s=31.0,
        day_counter=1, day_target=28,
        shadow={"result": "MATCH", "max_weight_delta": 1.1e-12},
        criteria={"shadow_reconciliation": {"ok": True, "detail": "1/1 days"}},
        composition_guard={"alert": False, "excluded": [], "ambiguous": [],
                           "excluded_in_top15": 0},
    ).to_dict()
    s.update(over)
    return s


# ---------------------------------------------------------------- rendering

def test_renders_healthy_snapshot():
    snap = healthy()
    light, reason = status_light(snap, now=NOW + 60)
    assert light == GREEN, (light, reason)
    html = render_page(snap, now=NOW + 60)
    assert "<!doctype html>" in html.lower()
    assert "BTCUSDT" in html and "GREEN" in html
    assert "812.44" in html
    # PnL must be present but explicitly not a criterion
    assert "NOT a success criterion" in html
    print("PASS dashboard_healthy")


def test_missing_status_is_red_not_a_crash():
    light, reason = status_light(None)
    assert light == RED and "not reporting" in reason
    html = render_page(None)          # must not raise on day one
    assert "RED" in html and "no status.json" in html
    print("PASS dashboard_missing")


def test_stale_status_forces_red():
    """STAGE10A §3: the dashboard must fail loud when its source goes quiet,
    or it becomes a reassurance machine."""
    snap = healthy()
    assert not is_stale(snap, now=NOW + DAY)          # 1 cycle: fine
    assert is_stale(snap, now=NOW + 2.5 * DAY)        # >2 cycles: stale
    light, reason = status_light(snap, now=NOW + 2.5 * DAY)
    assert light == RED, (light, reason)
    assert "stale" in reason
    assert "RED" in render_page(snap, now=NOW + 2.5 * DAY)
    print("PASS dashboard_stale_is_red")


def test_shadow_mismatch_day_is_red():
    """Stage 10 §3: a mismatch means the live path and the research path are
    different strategies. That is a stop, so it is RED, not AMBER."""
    snap = healthy(shadow={"result": "MISMATCH", "max_weight_delta": 4.2e-3,
                           "detail": "BTCUSDT 0.0515 vs 0.0473"})
    light, reason = status_light(snap, now=NOW + 60)
    assert light == RED and "MISMATCH" in reason
    html = render_page(snap, now=NOW + 60)
    assert "MISMATCH" in html and "0.0473" in html
    print("PASS dashboard_mismatch_is_red")


def test_testnet_reset_day_renders_and_does_not_fire_kill_switch():
    """NOTES 46.5: a reset must never masquerade as a 100% drawdown."""
    snap = healthy(
        testnet_resets=[{"ts": NOW - 100, "from": 812.4, "to": 5000.0,
                         "note": "balance reset detected; series re-baselined"}],
        baseline_equity=5000.0, drawdown=0.004)
    light, _ = status_light(snap, now=NOW + 60)
    assert light == GREEN, "a re-baselined reset is not a drawdown"
    html = render_page(snap, now=NOW + 60)
    assert "testnet reset" in html and "kill switch not fired" in html
    print("PASS dashboard_testnet_reset")


def test_composition_guard_alert_is_amber():
    """Stage 12 A.2: the §48.6 guard must be visible and must colour the page."""
    snap = healthy(composition_guard={
        "alert": True, "reason": "3 excluded instruments in the unfiltered top-15",
        "excluded": [{"symbol": "XAUUSDT", "reason": "seeded_exclusion_list"}],
        "ambiguous": [{"symbol": "BZUSDT",
                       "reason": "trading_but_absent_from_snapshot"}],
        "excluded_in_top15": 3})
    light, reason = status_light(snap, now=NOW + 60)
    assert light == AMBER, (light, reason)
    assert "composition guard" in reason
    html = render_page(snap, now=NOW + 60)
    assert "XAUUSDT" in html and "ALERT" in html
    print("PASS dashboard_composition_guard")


def test_kill_switch_unarmed_or_breached_is_red():
    assert status_light(healthy(kill_switch_armed=False), now=NOW)[0] == RED
    assert status_light(healthy(drawdown=0.31), now=NOW)[0] == RED
    assert status_light(healthy(halted=True, halt_reason="x"), now=NOW)[0] == RED
    print("PASS dashboard_killswitch_red")


def test_anomaly_feed_is_never_filtered():
    """STAGE10A §6: nothing may be filtered or summarised out of the feed."""
    snap = healthy(anomalies=[{"ts": "12:00", "msg": f"anomaly {i}"}
                              for i in range(12)])
    light, _ = status_light(snap, now=NOW + 60)
    assert light == AMBER
    html = render_page(snap, now=NOW + 60)
    for i in range(12):
        assert f"anomaly {i}" in html, i
    print("PASS dashboard_anomaly_feed")


def test_partial_snapshot_renders_no_data_yet():
    """Day one: almost everything missing. Must render, never crash."""
    snap = {"ts": NOW, "cycle_interval_s": DAY, "kill_switch_armed": True}
    html = render_page(snap, now=NOW + 10)
    assert "no data yet" in html or "no equity history yet" in html
    assert "flat" in html
    print("PASS dashboard_partial")


# ------------------------------------------------------------- atomic write

def test_status_write_is_atomic_no_torn_read(tmp_path):
    """A reader must see the previous snapshot or the next one, never half.

    Uses tmp_path rather than TemporaryDirectory: on Windows the reader thread
    can still hold a handle when an eager cleanup runs, which fails the test
    for a teardown reason rather than for the invariant under test.
    """
    p = tmp_path / "status.json"
    write_status(p, StatusSnapshot(ts=NOW, equity=1.0))
    stop = threading.Event()
    seen: list = []
    errors: list = []

    def reader():
        while not stop.is_set():
            got = read_status(p)
            if got is None:
                errors.append("torn or missing read")
            else:
                seen.append(got.get("equity"))
            time.sleep(0)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(120):
            write_status(p, StatusSnapshot(ts=NOW + i, equity=float(i),
                                           anomalies=[{"msg": "x" * 400}]))
    finally:
        stop.set()
        t.join(timeout=10)

    assert not t.is_alive(), "reader thread did not stop"
    assert not errors, errors[:5]
    assert len(seen) > 5, f"reader did not observe enough reads: {len(seen)}"
    # every observed value was a complete, valid snapshot
    assert all(isinstance(v, (int, float)) for v in seen), seen[:5]
    # no temp files left behind
    assert not list(tmp_path.glob(".status-*.tmp"))
    print(f"PASS dashboard_atomic_write ({len(seen)} concurrent reads, 0 torn)")


# ------------------------------------------------- the security static check

def test_ui_process_has_no_keys_and_no_exchange_client():
    """STAGE10A §0/§6: the UI holds no keys, imports no exchange client, and
    exposes no write endpoint. Checked statically so it cannot regress."""
    pkg = ROOT / "dashboard"
    files = sorted(pkg.glob("*.py"))
    assert files, "no dashboard sources found"

    banned_import = re.compile(r"\b(live\.client|requests|urllib\.request|"
                               r"http\.client|socket)\b")
    env_read = re.compile(r"(os\.environ|getenv)")
    key_word = re.compile(r"(?i)(api_?key|api_?secret|BINANCE_)")

    for f in files:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [n.name for n in node.names]
                elif node.module:
                    names = [node.module]
                for n in names:
                    assert not banned_import.search(n), (
                        f"{f.name} imports {n!r}: the UI must not reach the "
                        f"network or the exchange")
        # strip this module's own explanatory prose before scanning for reads
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert not env_read.search(code), f"{f.name} reads the environment"
        assert not key_word.search(code), f"{f.name} mentions key material"

        # GET only: no do_POST/do_PUT/do_DELETE/do_PATCH anywhere
        for verb in ("POST", "PUT", "DELETE", "PATCH", "HEAD"):
            assert f"do_{verb}" not in src, f"{f.name} defines do_{verb}"

    assert hasattr(DashboardHandler, "do_GET")
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert not hasattr(DashboardHandler, f"do_{verb}")
    print("PASS dashboard_no_keys_no_client")


def test_refuses_to_bind_beyond_loopback():
    with pytest.raises(ValueError, match="loopback"):
        serve(host="0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        serve(host="192.168.1.10")
    print("PASS dashboard_loopback_only")


# ------------------------------------------------------------ live serving

def test_serves_a_page_and_refuses_writes(tmp_path):
    status = tmp_path / "status.json"
    write_status(status, healthy())
    httpd = serve(status_path=status, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            body = r.read().decode()
            assert r.status == 200
        assert "BTCUSDT" in body and "GREEN" in body

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                    timeout=5) as r:
            assert r.read().decode().startswith("GREEN")

        # a write verb must not be served
        req = urllib.request.Request(f"http://127.0.0.1:{port}/",
                                     data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("POST was served")
        except urllib.error.HTTPError as e:
            assert e.code in (400, 501), e.code

        # missing status -> the page still serves, and /health reports RED
        os.unlink(status)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert "RED" in r.read().decode()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
            raise AssertionError("/health should be 503 when RED")
        except urllib.error.HTTPError as e:
            assert e.code == 503
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)
    print("PASS dashboard_serves")
