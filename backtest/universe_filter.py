"""
Stage 11 (NOTES 48): the crypto-only universe filter.

§47 found the universe rule -- "top 15 by point-in-time median quote volume"
-- had drifted into tokenised equities, ETFs, commodities and pre-market
instruments. The rule never changed; the market moved underneath it. This
module restores the rule to its intent by making "crypto asset" an explicit,
machine-checkable eligibility test instead of an assumption.

CLASSIFICATION IS FROM A COMMITTED SNAPSHOT, NOT A NETWORK CALL
---------------------------------------------------------------
`data/underlying_classes.json` is a dated snapshot of Binance exchangeInfo
metadata. Reading exchangeInfo live at decision time would make the universe
depend on a network round-trip and would make Test 26 non-hermetic. The
snapshot is committed; refresh it with tools/snapshot_underlying.py.

THE DISCRIMINATOR: contractType == "TRADIFI_PERPETUAL"  (NOTES 48.9)
--------------------------------------------------------------------
Binance's own label for a tokenised traditional-finance perpetual. Within the
snapshot it captures the whole TradFi block exactly -- EQUITY (21),
HK_EQUITY (6), COMMODITY (5) and the pre-IPO subset of PREMARKET (6) -- and
captures nothing else.

IT DOES NOT CATCH ALL EIGHT OF §47.1 ON ITS OWN, and the reason matters: the
snapshot is taken from TESTNET, which lists only a subset of production
instruments. Of §47.1's eight, only XAU, XAG and SPCX are on testnet and so
only those three are caught by metadata; SNDK, SKHYNIX, MU, SOXL and CL are
absent from testnet entirely and are caught ONLY by EXCLUDED_SYMBOLS. The
seeded list is therefore load-bearing, not belt-and-braces.

The open hole this leaves (NOTES 48.11): a FUTURE production TradFi listing
that testnet does not carry has no metadata, is not on the seeded list, and
would fall through as `historical_no_metadata` -> crypto. It would raise
neither an exclusion nor an ambiguity, so the §48.6 composition guard would
not fire on it either. `suspicious_absences()` below exists for exactly that
case: a symbol still trading but absent from the snapshot cannot be
"delisted before the snapshot", and is reported.

An EARLIER version of this module also excluded `underlyingType == "INDEX"`
and any `underlyingSubType` containing TradFi/Pre-IPO/Pre-Market. Both were
over-broad against the written specification and both were WRONG:

  * the four INDEX symbols (ALL, BLUEBIRD, BTCDOM, DEFI) are indices on
    CRYPTO assets, all contractType PERPETUAL. The spec excludes an "index on
    non-crypto assets"; none of these is one.
  * OMGUSDT (OMG Network) and SOMIUSDT (Somnia) are underlyingType COIN,
    contractType PERPETUAL -- genuine crypto tokens -- that happen to carry a
    TradFi/Pre-Market subtype tag in Binance's metadata. The subtype is not a
    reliable standalone signal.

That over-breadth made the no-op proof fail on 1,508 days (NOTES 48.8). The
correction is recorded in NOTES 48.9 and is justified by the specification
text, which predates the proof -- not by the diff the proof produced.

THE THREE OUTCOMES
------------------
  CRYPTO      -- a USDT perpetual that is not a TradFi instrument
  NON_CRYPTO  -- contractType TRADIFI_PERPETUAL, or on the seeded list
  AMBIGUOUS   -- an underlyingType or contractType value this module has never
                 seen. EXCLUDED and logged: the next weird listing will not be
                 as obvious as tokenised gold.

A symbol ABSENT from the snapshot is a fourth case and is NOT ambiguity: it
delisted before the snapshot was taken. Live it cannot arise (the live
universe is built from exchangeInfo, so an absent symbol is not tradeable).
In historical replay it is treated as crypto unless the seeded list fires --
because excluding it would retroactively delete legitimately traded crypto
symbols from the past and break the equivalence NOTES 48.5 exists to prove.
Every such fallback is COUNTED and reported, never silent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Stage 14 A.1 / NOTES 51.1 -- TWO metadata sources with fixed precedence.
# Production is authoritative for CLASSIFICATION: it sees every listed
# instrument, which is exactly the 48.11 blindness being closed (180
# TradFi perpetuals against testnet's 40). Testnet remains authoritative
# for TRADEABILITY -- what the paper book can actually hold -- and is the
# fallback for anything production does not carry.
#
# Both are USER-SUPPLIED FILES. Nothing here fetches production metadata;
# no production hostname exists anywhere in this codebase.
SNAPSHOT_PATH = ROOT / "data" / "underlying_classes.json"            # testnet
PRODUCTION_SNAPSHOT_PATH = ROOT / "data" / "underlying_classes_production.json"

# NOTES 51.3: past this age the dashboard composition line goes AMBER with
# "metadata snapshot stale -- refresh advised". A prompt, never an action:
# the guard observes and alerts and never auto-amends (48.6).
SNAPSHOT_STALE_MS = 30 * 86_400_000

CRYPTO = "crypto"
NON_CRYPTO = "non_crypto"
AMBIGUOUS = "ambiguous"

# THE discriminator. Binance's own label for a tokenised TradFi perpetual.
TRADFI_CONTRACT_TYPE = "TRADIFI_PERPETUAL"

# How close a decision time must be to the metadata snapshot for the
# NOTES 48.11 recency test to be answerable at all. The research store
# lags the live snapshot by weeks, so this must comfortably exceed that
# gap while still excluding historical replay by years.
CONTEMPORANEOUS_MS = 90 * 86_400_000

# Values observed in the 2026-08-29 snapshot. Anything outside these is
# AMBIGUOUS -- unknown means excluded and logged, never guessed.
KNOWN_UNDERLYING_TYPES = frozenset({
    "COIN", "EQUITY", "HK_EQUITY", "COMMODITY", "INDEX", "PREMARKET",
    # NOTES 51.2: the production snapshot carries regional equity labels the
    # testnet one never had. Same kind as HK_EQUITY, which was already here.
    "KR_EQUITY", "CN_EQUITY",
})
KNOWN_CONTRACT_TYPES = frozenset({
    "PERPETUAL", TRADFI_CONTRACT_TYPE, "CURRENT_QUARTER", "NEXT_QUARTER",
    "CURRENT_WEEK", "NEXT_WEEK", "CURRENT_QUARTER DELIVERING",
})

# Seeded from NOTES 47.1. Belt and braces: the contractType test above already
# catches all eight, and this list keeps catching them if Binance relabels.
EXCLUDED_SYMBOLS = frozenset({
    "SNDKUSDT",     # SanDisk -- equity
    "SKHYNIXUSDT",  # SK Hynix -- equity
    "MUUSDT",       # Micron -- equity
    "SPCXUSDT",     # SpaceX -- pre-IPO
    "SOXLUSDT",     # leveraged semiconductor ETF
    "XAUUSDT",      # gold
    "XAGUSDT",      # silver
    "CLUSDT",       # crude oil
})


@dataclass(frozen=True)
class Verdict:
    symbol: str
    klass: str              # CRYPTO | NON_CRYPTO | AMBIGUOUS
    reason: str
    from_metadata: bool     # False == the historical no-metadata fallback

    @property
    def eligible(self) -> bool:
        return self.klass == CRYPTO


def _read(p: Path) -> dict:
    if not p.exists():
        return {"snapshot_date": None, "snapshot_ts": None, "symbols": {}}
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_snapshot(path: str | None = None) -> dict:
    """The merged metadata view: production overlaid on testnet.

    Production wins on every symbol it carries (NOTES 51.1); testnet fills in
    anything production does not, which in practice is nothing but costs
    nothing to keep. `snapshot_ts` is the PRODUCTION timestamp when production
    is present, because that is the one whose staleness matters for seeing new
    listings.
    """
    if path:                       # explicit override: single source, as given
        return _read(Path(path))
    testnet = _read(SNAPSHOT_PATH)
    prod = _read(PRODUCTION_SNAPSHOT_PATH)
    if not prod.get("symbols"):
        return testnet
    merged = dict(testnet.get("symbols") or {})
    merged.update(prod["symbols"])
    return {
        "snapshot_date": prod.get("snapshot_date"),
        "snapshot_ts": prod.get("snapshot_ts"),
        "source": prod.get("source"),
        "testnet_snapshot_ts": testnet.get("snapshot_ts"),
        "n_production": len(prod["symbols"]),
        "n_testnet_only": len(set(testnet.get("symbols") or {}) - set(prod["symbols"])),
        "symbols": merged,
    }


def snapshot_is_stale(snapshot: dict | None = None, now_ms: int | None = None) -> bool:
    """NOTES 51.3: True once the metadata is older than SNAPSHOT_STALE_MS.
    Drives the dashboard AMBER prompt; changes no classification."""
    import time as _t
    snap = snapshot if snapshot is not None else load_snapshot()
    ts = snap.get("snapshot_ts")
    if not ts:
        return True
    now = now_ms if now_ms is not None else int(_t.time() * 1000)
    return (now - int(ts)) > SNAPSHOT_STALE_MS


def classify(symbol: str, snapshot: dict | None = None) -> Verdict:
    """Classify one symbol. Pure: same inputs, same verdict, no I/O beyond the
    cached snapshot read."""
    snap = snapshot if snapshot is not None else load_snapshot()
    meta = snap.get("symbols", {}).get(symbol)

    if symbol in EXCLUDED_SYMBOLS:
        return Verdict(symbol, NON_CRYPTO, "seeded_exclusion_list",
                       from_metadata=meta is not None)

    if meta is None:
        # Absent from the snapshot: delisted before it was taken (NOTES 48.4).
        # Not ambiguity. Counted by the caller.
        return Verdict(symbol, CRYPTO, "historical_no_metadata",
                       from_metadata=False)

    utype = meta.get("underlyingType")
    ctype = meta.get("contractType")

    # NOTES 51.2: the contractType discriminator is checked FIRST. A future
    # regional label like XX_EQUITY on a TradFi contract is then classified
    # correctly on its contract type instead of falling into ambiguity, while
    # an unknown type on a PERPETUAL contract still goes ambiguous. Strictly
    # better coverage, no loss of conservatism.
    if ctype == TRADFI_CONTRACT_TYPE:
        return Verdict(symbol, NON_CRYPTO,
                       f"contractType={ctype} (underlyingType={utype})", True)
    if utype not in KNOWN_UNDERLYING_TYPES:
        return Verdict(symbol, AMBIGUOUS,
                       f"underlying_ambiguous:underlyingType={utype!r}", True)
    if ctype not in KNOWN_CONTRACT_TYPES:
        return Verdict(symbol, AMBIGUOUS,
                       f"underlying_ambiguous:contractType={ctype!r}", True)
    return Verdict(symbol, CRYPTO, f"underlyingType={utype}", True)


def filter_universe(
    symbols: list[str], snapshot: dict | None = None,
    last_bar_ms: dict[str, int] | None = None,
    reference_ms: int | None = None,
) -> tuple[list[str], list[Verdict]]:
    """(eligible symbols, verdicts for everything excluded).

    Order is preserved so a caller's volume ranking survives untouched.

    Pass `last_bar_ms` (symbol -> last bar close_time) to enable the NOTES
    48.11 recency test, and pass it EVERYWHERE a real universe is built. A
    symbol that is absent from the metadata snapshot but is still trading
    cannot be "delisted before the snapshot"; it is an instrument the snapshot
    cannot see, and it is excluded as `underlying_ambiguous` under the §2.3
    conservative default rather than admitted by the §48.4 fallback.

    That exemption was the hole: without this test the filter admitted AMD,
    Brent crude, DRAM, a South Korea ETF, Marvell, Nebius and Samsung as
    "crypto" purely because testnet does not list them (NOTES 48.11).
    """
    suspicious = set(
        suspicious_absences(last_bar_ms, snapshot, reference_ms)
        if last_bar_ms else ()
    )
    keep, dropped = [], []
    for s in symbols:
        v = classify(s, snapshot)
        if s in suspicious:
            v = Verdict(s, AMBIGUOUS,
                        "underlying_ambiguous:trading_but_absent_from_snapshot",
                        from_metadata=False)
        if v.eligible:
            keep.append(s)
        else:
            dropped.append(v)
    return keep, dropped


def suspicious_absences(
    symbols_with_last_bar_ms: dict[str, int], snapshot: dict | None = None,
    reference_ms: int | None = None, grace_ms: int = 14 * 86_400_000,
) -> list[str]:
    """
    Symbols that are STILL TRADING but absent from the metadata snapshot.

    The §48.4 fallback assumes an absent symbol delisted before the snapshot
    was taken. A symbol still trading at the end of the data contradicts that:
    it is trading somewhere the snapshot does not cover. Since the snapshot
    comes from TESTNET and testnet carries a subset of production instruments,
    this is exactly how a production-only TradFi listing slips through the
    filter unnoticed (NOTES 48.11).

    `reference_ms` is "now" for the data being classified, and defaults to the
    latest last-bar in the input. It must NOT default to the snapshot
    timestamp: the research store ends weeks behind the live snapshot, so
    every symbol in it would look delisted and the guard would never fire --
    which is exactly the bug this signature replaces.

    Reported so it cannot be silent. Never auto-excludes: the guard observes
    and alerts, it does not amend the rule (NOTES 48.6).
    """
    snap = snapshot if snapshot is not None else load_snapshot()
    if not snap.get("symbols") or not symbols_with_last_bar_ms:
        return []
    ref = (reference_ms if reference_ms is not None
           else max(symbols_with_last_bar_ms.values()))

    # The test is only MEANINGFUL when the data and the metadata are
    # contemporaneous. Applied to a historical replay it would flag every
    # symbol that was alive then and delisted since -- LENDUSDT, a genuine
    # 2020 crypto token, was the one that caught this -- turning a
    # forward-looking guard into a retroactive deletion of the past. So when
    # the reference predates the snapshot era, the question "is this trading
    # now but missing from the snapshot?" simply cannot be asked, and the
    # §48.4 fallback stands.
    snap_ts = snap.get("snapshot_ts")
    if snap_ts and ref < snap_ts - CONTEMPORANEOUS_MS:
        return []

    known = snap["symbols"]
    return sorted(
        s for s, last in symbols_with_last_bar_ms.items()
        if s not in known and s not in EXCLUDED_SYMBOLS and last >= ref - grace_ms
    )


def fallback_count(symbols: list[str], snapshot: dict | None = None) -> int:
    """How many symbols were admitted only by the NOTES 48.4 historical
    fallback. Reported by the no-op proof so the fallback is never silent."""
    return sum(1 for s in symbols
               if classify(s, snapshot).reason == "historical_no_metadata")
