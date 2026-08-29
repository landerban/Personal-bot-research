"""
Stage 14 §B.4: tests for the standalone runner.

The single-instance lock gets the most attention because it guards the
highest-risk failure of "just double-click it": two supervisors placing the
same orders twice.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xsmom import schedule as S  # noqa: E402
from xsmom.lock import AlreadyRunning, SingleInstanceLock, pid_alive  # noqa: E402
from xsmom.supervisor import BACKOFF_MAX_S, ConfigError, Supervisor  # noqa: E402

UTC = timezone.utc


# ------------------------------------------------------------------- lock

def test_second_launch_refuses_loudly(tmp_path):
    """Two supervisors would place the same orders twice. The second must
    REFUSE and say which pid holds it -- not exit quietly, not proceed."""
    p = tmp_path / "supervisor.lock"
    other = _live_foreign_pid()
    try:
        p.write_text(json.dumps({"pid": other.pid, "started_at": time.time(),
                                 "host": "x"}), encoding="utf-8")
        second = SingleInstanceLock(p)
        with pytest.raises(AlreadyRunning) as e:
            second.acquire()
        msg = str(e.value)
        assert str(other.pid) in msg, msg
        assert "second" in msg.lower() and "twice" in msg.lower(), msg
        assert p.exists(), "the holder's lock must survive a refused acquire"
    finally:
        other.terminate()
        other.wait(timeout=10)
    print("PASS runner_lock_refuses_second")


def test_stale_lock_from_a_dead_pid_is_reclaimed(tmp_path):
    """A power cut or kill -9 leaves a lock behind. The next start must
    reclaim it rather than refusing forever."""
    p = tmp_path / "supervisor.lock"
    dead = _dead_pid()
    p.write_text(json.dumps({"pid": dead, "started_at": time.time(),
                             "host": "x"}), encoding="utf-8")
    lock = SingleInstanceLock(p)
    info = lock.acquire()
    try:
        assert info.pid == os.getpid()
        assert info.reclaimed_from == dead
    finally:
        lock.release()
    print(f"PASS runner_lock_reclaims_stale (dead pid {dead})")


def test_release_never_deletes_another_instances_lock(tmp_path):
    """If we were reclaimed from, our release must not remove THEIR lock."""
    p = tmp_path / "supervisor.lock"
    lock = SingleInstanceLock(p)
    lock.acquire()
    p.write_text(json.dumps({"pid": os.getpid() + 100_000,
                             "started_at": time.time(), "host": "other"}),
                 encoding="utf-8")
    lock.release()
    assert p.exists(), "released a lock that belonged to another instance"
    print("PASS runner_lock_release_is_owner_only")


def test_pid_alive_agrees_with_reality():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(_dead_pid()) is False
    assert pid_alive(-1) is False
    print("PASS runner_pid_alive")


def _live_foreign_pid():
    """A live process that is NOT us. The lock's guard is cross-process, so
    same-pid re-acquisition (a restart inside one process) is deliberately
    allowed and cannot exercise it."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])


def _dead_pid() -> int:
    """A pid that has certainly exited: spawn a trivial child and reap it."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    for _ in range(50):
        if not pid_alive(p.pid):
            return p.pid
        time.sleep(0.05)
    return 999_999_37          # fallback: almost certainly unused


# --------------------------------------------------------------- schedule

def test_cycle_classification_matches_the_registered_policy():
    """NOTES 51.4, fixed before the runner was built."""
    sched = datetime(2026, 8, 30, 0, 0, 15, tzinfo=UTC)
    assert S.classify(sched, sched) == S.ON_TIME
    assert S.classify(sched + timedelta(minutes=4), sched) == S.ON_TIME
    assert S.classify(sched + timedelta(hours=1), sched) == S.LATE
    assert S.classify(sched + timedelta(hours=1, minutes=59), sched) == S.LATE
    assert S.classify(sched + timedelta(hours=2, minutes=5), sched) == S.MISSED
    assert S.classify(sched + timedelta(hours=14), sched) == S.MISSED
    print("PASS runner_cycle_classification")


def test_missed_day_pauses_the_clock_and_a_late_day_counts():
    """The distinction NOTES 51.4 exists to fix: a host that was switched off
    is not a failure of the machine under test."""
    st = S.ClockState()
    st.record_cycle("2026-08-30", S.ON_TIME)
    st.record_cycle("2026-08-31", S.LATE)
    assert st.day_counter == 2, "a late cycle must COUNT"
    st.record_missed("2026-09-01")
    assert st.day_counter == 2, "a missed day must not count..."
    assert st.missed_days == ["2026-09-01"]
    st.record_cycle("2026-09-02", S.ON_TIME)
    assert st.day_counter == 3, "...and must not reset either"
    assert st.late_days == ["2026-08-31"]
    print("PASS runner_missed_pauses_late_counts")


def test_only_the_46_rules_reset_the_clock():
    st = S.ClockState(day_counter=9)
    st.record_missed("2026-09-01")
    assert st.day_counter == 9
    st.reset("2026-09-02", "unrecovered crash")
    assert st.day_counter == 0
    assert st.resets[-1]["counter_was"] == 9
    assert st.resets[-1]["reason"] == "unrecovered crash"
    print("PASS runner_only_46_resets")


def test_record_cycle_is_idempotent_for_one_day():
    """A restart inside the same UTC day must not count the day twice."""
    st = S.ClockState()
    st.record_cycle("2026-08-30", S.ON_TIME)
    st.record_cycle("2026-08-30", S.ON_TIME)
    assert st.day_counter == 1
    print("PASS runner_cycle_idempotent")


def test_due_cycle_answers_the_question_a_woken_machine_asks():
    st = S.ClockState()
    sched = S.scheduled_for(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))

    # before today's instant -> not due yet. (Five minutes EARLIER than this
    # lands on the previous UTC day, whose own cycle is then correctly
    # 'missed' -- due_cycle always asks about the day it is standing in.)
    should, kind, date = S.due_cycle(sched - timedelta(seconds=5), st)
    assert (should, kind) == (False, "not_due"), (should, kind)
    assert S.due_cycle(sched - timedelta(minutes=5), st)[1] == S.MISSED

    should, kind, date = S.due_cycle(sched + timedelta(hours=1), st)
    assert (should, kind, date) == (True, S.LATE, "2026-08-30")

    should, kind, _ = S.due_cycle(sched + timedelta(hours=6), st)
    assert (should, kind) == (False, S.MISSED)

    st.record_cycle("2026-08-30", S.ON_TIME)
    should, kind, _ = S.due_cycle(sched + timedelta(hours=1), st)
    assert (should, kind) == (False, "already_ran")
    print("PASS runner_due_cycle")


def test_clock_state_round_trips(tmp_path):
    p = tmp_path / "clock.json"
    st = S.ClockState(day_counter=4)
    st.record_missed("2026-09-01")
    st.save(p)
    back = S.ClockState.load(p)
    assert back.day_counter == 4 and back.missed_days == ["2026-09-01"]
    assert S.ClockState.load(tmp_path / "nope.json").day_counter == 0
    print("PASS runner_clock_round_trip")


# ------------------------------------------------------------- supervisor

def test_supervisor_restarts_a_dead_dashboard_with_backoff(monkeypatch):
    """The dashboard dying must never take the trader down, and must not spin."""
    sup = Supervisor(run_dashboard=True)
    starts = []

    class DeadThread:
        def is_alive(self):
            return False

    def fake_start():
        starts.append(time.time())
        sup._dash_thread = DeadThread()

    monkeypatch.setattr(sup, "_start_dashboard", fake_start)
    monkeypatch.setattr(sup.stop, "wait", lambda s: False)   # no real sleeping
    sup._dash_thread = DeadThread()

    for _ in range(3):
        sup._supervise_dashboard()
    assert sup.dashboard_restarts == 3, sup.dashboard_restarts
    assert len(starts) == 3

    # a healthy dashboard resets the backoff rather than letting it creep up
    sup._dash_backoff = 120.0
    sup._dash_thread = type("Alive", (), {"is_alive": lambda self: True})()
    sup._supervise_dashboard()
    assert sup._dash_backoff == 5.0
    assert BACKOFF_MAX_S == 300.0
    print("PASS runner_dashboard_backoff")


def test_supervisor_gives_up_on_a_config_error(monkeypatch, tmp_path):
    """Exit 3 and stop. A supervisor that restart-loops on a bad config burns
    the machine and hides the message."""
    import xsmom.supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "LOCK_PATH", tmp_path / "s.lock")

    def boom():
        raise ConfigError("no testnet API credentials in the environment")

    monkeypatch.setattr(sup_mod, "preflight", boom)
    rc = sup_mod.Supervisor(run_dashboard=False).run()
    assert rc == 3, rc
    assert not (tmp_path / "s.lock").exists(), "lock must be released on exit"
    print("PASS runner_config_error_exit3")


def test_supervisor_refuses_when_another_instance_holds_the_lock(
        monkeypatch, tmp_path):
    import xsmom.supervisor as sup_mod

    lock_path = tmp_path / "s.lock"
    monkeypatch.setattr(sup_mod, "LOCK_PATH", lock_path)
    other = _live_foreign_pid()
    try:
        lock_path.write_text(json.dumps(
            {"pid": other.pid, "started_at": time.time(), "host": "x"}),
            encoding="utf-8")
        rc = sup_mod.Supervisor(run_dashboard=False).run()
        assert rc == 2, rc
        assert lock_path.exists(), "the holder's lock must survive"
    finally:
        other.terminate()
        other.wait(timeout=10)
    print("PASS runner_refuses_on_held_lock")


def test_tick_records_a_missed_day_without_running_a_cycle(monkeypatch, tmp_path):
    """Clock injection: a machine woken 6h late must log missed_cycle, hold
    the book, and leave the counter untouched."""
    import xsmom.supervisor as sup_mod

    clock_path = tmp_path / "clock.json"
    monkeypatch.setattr(sup_mod, "CLOCK_PATH", clock_path)
    S.ClockState(day_counter=5).save(clock_path)

    sched = S.scheduled_for(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    late = sched + timedelta(hours=6)
    sup = Supervisor(run_dashboard=False, clock=lambda: late)
    monkeypatch.setattr(sup, "beat", lambda: None)
    ran = []
    monkeypatch.setattr(sup, "run_cycle_now",
                        lambda kind, date: ran.append((kind, date)))
    sup.tick()

    assert ran == [], "a missed cycle must NOT run"
    st = S.ClockState.load(clock_path)
    assert st.day_counter == 5, "missed day must not change the counter"
    assert st.missed_days == ["2026-08-30"]
    print("PASS runner_tick_missed_day")


def test_tick_runs_a_late_cycle_inside_the_grace_window(monkeypatch, tmp_path):
    import xsmom.supervisor as sup_mod

    clock_path = tmp_path / "clock.json"
    monkeypatch.setattr(sup_mod, "CLOCK_PATH", clock_path)
    S.ClockState(day_counter=5).save(clock_path)

    sched = S.scheduled_for(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    sup = Supervisor(run_dashboard=False,
                     clock=lambda: sched + timedelta(hours=1))
    monkeypatch.setattr(sup, "beat", lambda: None)
    ran = []
    monkeypatch.setattr(sup, "run_cycle_now",
                        lambda kind, date: ran.append((kind, date)) or True)
    sup.tick()
    assert ran == [(S.LATE, "2026-08-30")], ran
    print("PASS runner_tick_late_cycle")


def test_a_crashed_cycle_is_not_counted_as_a_day(monkeypatch, tmp_path):
    """A cycle that raises must be logged loudly and must NOT advance the
    28-day counter."""
    import xsmom.supervisor as sup_mod

    clock_path = tmp_path / "clock.json"
    monkeypatch.setattr(sup_mod, "CLOCK_PATH", clock_path)
    S.ClockState(day_counter=5).save(clock_path)

    sched = S.scheduled_for(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    sup = Supervisor(run_dashboard=False, clock=lambda: sched)
    monkeypatch.setattr(sup, "beat", lambda: None)

    def boom(kind, date):
        raise RuntimeError("exchange unreachable")

    monkeypatch.setattr(sup, "run_cycle_now", boom)
    sup.tick()                       # must not propagate
    assert S.ClockState.load(clock_path).day_counter == 5
    print("PASS runner_crashed_cycle_not_counted")
