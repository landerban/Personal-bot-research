#!/usr/bin/env python3
"""
Stage 10 (NOTES 46.8): prove no key material is committed to this repo.

  python tools/scan_secrets.py            # scan tracked files, exit 1 on a hit

Runs standalone and from the test suite. The rule it enforces is the standing
one: keys live in environment variables sourced from OUTSIDE the repository,
never in a committed file, never in a log line.

Two classes of hit:

  * HIGH-ENTROPY LITERAL -- a 40+ char base62 run sitting next to a
    key/secret-shaped name, or assigned to one. Binance keys are 64 chars of
    base62, so this is the shape that matters here.
  * KEY-SHAPED ASSIGNMENT -- any `...KEY... = "<non-empty literal>"` or
    `...SECRET... = "<literal>"` that is not obviously a placeholder.

Known-safe values are listed explicitly rather than pattern-excused, so a new
one has to be added deliberately and shows up in a diff.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Values that ARE in the repo on purpose and are not credentials.
# Each needs a reason; an unexplained addition here is the thing to catch in
# review.
ALLOWED = {
    # tests/test_live.py: Binance's own published HMAC example secret, used to
    # pin the signing implementation against the documented vector.
    "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j",
    # the documented expected signature for that vector
    "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71",
    "test-key", "test-secret", "", "None",
}

PLACEHOLDER = re.compile(
    r"^(your[-_ ]?|xxx|<|\.\.\.|changeme|placeholder|example|dummy|fake|sk-\.\.\.)",
    re.I,
)

# a long base62 run -- the shape of a Binance key or secret
LITERAL = re.compile(r"""['"]([A-Za-z0-9]{40,})['"]""")
# NAME = "literal", where NAME smells like a credential
ASSIGN = re.compile(
    r"""(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:KEY|SECRET|TOKEN|PASSWD|PASSWORD)[A-Za-z0-9_]*)\s*"""
    r"""[:=]\s*['"]([^'"]*)['"]"""
)
NAME_NEAR = re.compile(r"(?i)(key|secret|token|passwd|password|credential)")
# SCREAMING_SNAKE -- the shape of an environment-variable NAME, never of a
# Binance credential (which is mixed-case base62).
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")

SKIP_SUFFIX = {".db", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico"}


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.splitlines()
    return [ROOT / f for f in out if f]


def scan_text(path: Path, text: str) -> list[str]:
    hits: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for m in ASSIGN.finditer(line):
            name, val = m.group(1), m.group(2)
            if val in ALLOWED or PLACEHOLDER.match(val) or len(val) < 8:
                continue
            # an env-var LOOKUP is the correct pattern, not a leak
            if "environ" in line or "getenv" in line:
                continue
            # ENV_KEY = "BINANCE_TESTNET_API_KEY" is a variable NAME, not a
            # credential. Real Binance keys are mixed-case base62, so an
            # all-caps SCREAMING_SNAKE value cannot be one.
            if ENV_NAME.fullmatch(val):
                continue
            hits.append(f"{path.relative_to(ROOT)}:{i}: key-shaped assignment "
                        f"{name}=<{len(val)} chars>")
        for m in LITERAL.finditer(line):
            val = m.group(1)
            if val in ALLOWED or PLACEHOLDER.match(val):
                continue
            if entropy(val) < 3.5:
                continue
            has_upper = any(c.isupper() for c in val)
            has_lower = any(c.islower() for c in val)
            has_digit = any(c.isdigit() for c in val)
            if not (has_upper and has_lower and has_digit):
                continue
            if len(val) >= 55 or NAME_NEAR.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{i}: high-entropy "
                            f"literal ({len(val)} chars, H={entropy(val):.2f})")
    return hits


def scan() -> list[str]:
    hits: list[str] = []
    for f in tracked_files():
        if f.suffix.lower() in SKIP_SUFFIX or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        hits.extend(scan_text(f, text))
    return hits


def main() -> int:
    hits = scan()
    if hits:
        print("SECRET SCAN FAILED -- key material may be committed:")
        for h in hits:
            print("  " + h)
        print("\nKeys belong in environment variables sourced from OUTSIDE the "
              "repo (NOTES 46.8). If a hit is a deliberate published test "
              "vector, add it to ALLOWED with a reason.")
        return 1
    print(f"secret scan clean: {len(tracked_files())} tracked files, no key "
          f"material found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
