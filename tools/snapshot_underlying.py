#!/usr/bin/env python3
"""
Stage 11 2.2: snapshot Binance's underlying-type metadata to a committed file.

  python tools/snapshot_underlying.py

The crypto-only filter classifies symbols from exchangeInfo metadata. Reading
that live at decision time would make the universe depend on a network call,
and would make Test 26 non-hermetic and non-reproducible. So the metadata is
snapshotted, dated, and committed; the classifier reads the snapshot.

Read-only, unsigned, testnet host only. Metadata is not return data: this
touches no price series and no split.

Re-run to refresh. The snapshot's date is part of the record -- a symbol that
listed after it is unknown to the classifier and is handled by the ambiguity
rule (NOTES 48.3), not by a silent default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest import runner  # noqa: E402
from live.client import TestnetClient  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "underlying_classes.json"

# Fields kept. Everything else in exchangeInfo is execution detail that the
# universe rule has no business depending on.
FIELDS = ("underlyingType", "underlyingSubType", "contractType",
          "quoteAsset", "baseAsset", "status", "onboardDate")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--from-file", default=None,
                    help="derive from a USER-SUPPLIED exchangeInfo dump "
                         "instead of the testnet API (Stage 14 A.1). Nothing "
                         "in this codebase fetches production metadata; the "
                         "file arrives out of band.")
    ap.add_argument("--source-label", default=None)
    a = ap.parse_args()

    if a.from_file:
        info = json.loads(Path(a.from_file).read_text(encoding="utf-8"))
        source = a.source_label or f"user-supplied file: {a.from_file}"
        base_ts = info.get("serverTime")
    else:
        c = TestnetClient(api_key="", api_secret="")
        info = c.request("GET", "/fapi/v1/exchangeInfo")
        source = c.base_url + "/fapi/v1/exchangeInfo"
        base_ts = None
    rows = {}
    for s in info.get("symbols", []):
        rows[s["symbol"]] = {
            k: (list(s[k]) if isinstance(s.get(k), list) else s.get(k))
            for k in FIELDS
        }

    counts: dict[str, int] = {}
    for r in rows.values():
        counts[r.get("underlyingType") or "(absent)"] = (
            counts.get(r.get("underlyingType") or "(absent)", 0) + 1)

    ts = int(base_ts) if base_ts else int(time.time() * 1000)
    out = {
        "snapshot_ts": ts,
        "snapshot_date": datetime.fromtimestamp(
            ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
        "source": source,
        "git_commit": runner.git_state()[0],
        "n_symbols": len(rows),
        "underlying_type_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "symbols": dict(sorted(rows.items())),
    }
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, sort_keys=False) + "\n",
                    encoding="utf-8")
    rel = path.resolve()
    try:
        rel = rel.relative_to(ROOT)
    except ValueError:
        pass
    print(f"wrote {rel}  ({len(rows)} symbols, "
          f"{path.stat().st_size:,} bytes)")
    print("underlyingType distribution:")
    for k, v in out["underlying_type_counts"].items():
        print(f"  {k:<14} {v:>5}")


if __name__ == "__main__":
    main()
