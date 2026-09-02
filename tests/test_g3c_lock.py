"""
NOTES 70.4/70.5 — the G3-C specification lock. The run stage may not
proceed on drifted code without a new pre-registration.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 70.6.11: g3/timing.py joins the set; backtest/costs.py leaves it
# (the economic gate was removed by 70.6.9).
LOCKED = {"g3/timing.py", "g3/features.py", "g3/models.py",
          "g3/calibration.py", "g3/sequential.py", "g3/eval.py",
          "rcm/eval_ic.py"}


def test_g3_lock_hashes_match_the_files():
    """70.4: LOCK-G3 lines in the ledger pin the spec and evaluator
    code; dict() keeps the LAST lock line per file so a future
    re-registration supersedes cleanly."""
    notes = (ROOT / "NOTES.md").read_text(encoding="utf-8")
    locks = dict(re.findall(r"^LOCK-G3 (\S+) sha256=([0-9a-f]{64})",
                            notes, re.MULTILINE))
    # dict() keeps the LAST line per file: 70.7 supersedes 70.5. The
    # superseded 70.5 set included backtest/costs.py; only the CURRENT
    # set is asserted and verified.
    locks = {k: v for k, v in locks.items() if k in LOCKED}
    assert set(locks) == LOCKED, locks
    for rel, cited in locks.items():
        actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert actual == cited, (
            f"{rel} changed after the 70.5 lock - the pre-registration "
            f"is void; re-register before any run")
    print("PASS g3c_lock_immutability")


def test_g3_trial1_pre_registered():
    """70.4: trial 1 logged pre-registered with the budget untouched."""
    notes = (ROOT / "NOTES.md").read_text(encoding="utf-8")
    assert ("G3-TRIAL-1 status=pre-registered attempt_id=1 "
            "valid_trial_count=0") in notes
    print("PASS g3c_trial1_preregistered")
