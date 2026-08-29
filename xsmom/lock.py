"""
Stage 14 B.2.4: the single-instance lock.

THE FAILURE THIS EXISTS TO PREVENT
----------------------------------
"Just double-click it" plus a scheduled task that also starts at logon and at
boot is a recipe for two supervisors running at once. Two supervisors means
two cycle schedulers means TWO TRADERS PLACING THE SAME ORDERS -- the book
ends up at double size, reconcile sees a position it did not intend, and both
instances fight to correct it.

This is the highest-risk failure mode of the whole runner, so the lock is
mandatory, refuses LOUDLY rather than silently exiting, and has its own tests.

HOW
---
A lock file holding the PID and start time. Acquiring it means:
  * no file            -> take it
  * file with a LIVE pid that is not us -> REFUSE, and say which pid
  * file with a DEAD pid (crash, power cut, kill -9) -> reclaim it and say so
Liveness is checked with the OS, not with a timeout, so a long-running healthy
instance is never mistaken for a stale one.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Another live instance holds the lock."""


@dataclass
class LockInfo:
    pid: int
    started_at: float
    host: str
    reclaimed_from: int | None = None


def pid_alive(pid: int) -> bool:
    """True if a process with this pid currently exists.

    Windows and POSIX differ, and both matter: the project runs on Windows but
    the tests and any future POSIX deployment need the same semantics.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)          # signal 0: existence check, no signal sent
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # exists, owned by someone else
    return True


class SingleInstanceLock:
    """Context manager. Raises AlreadyRunning if a live instance holds it."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.info: LockInfo | None = None

    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def acquire(self) -> LockInfo:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read()
        reclaimed = None
        if existing:
            other = int(existing.get("pid", -1))
            if other != os.getpid() and pid_alive(other):
                raise AlreadyRunning(
                    f"another xsmom supervisor is already running "
                    f"(pid {other}, started "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(existing.get('started_at', 0)))}). "
                    f"Refusing to start a second one: two supervisors would "
                    f"place the same orders twice. Stop that one first, or "
                    f"delete {self.path} only if you are certain it is dead."
                )
            if other != os.getpid():
                reclaimed = other
        self.info = LockInfo(pid=os.getpid(), started_at=time.time(),
                             host=os.environ.get("COMPUTERNAME")
                             or os.uname().nodename if hasattr(os, "uname")
                             else "unknown",
                             reclaimed_from=reclaimed)
        self.path.write_text(json.dumps({
            "pid": self.info.pid, "started_at": self.info.started_at,
            "host": self.info.host, "reclaimed_from": reclaimed,
        }, indent=1), encoding="utf-8")
        return self.info

    def release(self) -> None:
        """Remove the lock, but only if it is still ours -- never delete an
        instance that reclaimed it from us."""
        cur = self._read()
        if cur and int(cur.get("pid", -1)) == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self.info = None

    def __enter__(self) -> LockInfo:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
