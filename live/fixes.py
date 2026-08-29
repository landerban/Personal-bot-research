"""
The four Phase-2 fixes (NOTES 46.4 / STAGE10 §4), deferred at Stage 2e §10 to
"before live" and now due.

None is optional before real money, and each one guards a failure that the
backtester cannot represent because the backtester has no partial fills, no
rejected legs, no timeouts and no stops.

  1. MULTI-LEG ATOMICITY   a rebalance is several orders; some can fail. The
                           filled book must be checked against the target and
                           repaired or flattened, not assumed.
  2. STOP CASCADE          a stop firing changes the book behind the
                           strategy's back. It must trigger a reconcile and a
                           re-hedge, not a log line.
  3. FUNDING RECONSTRUCTION a settlement is charged on the position held AT
                           THAT INSTANT, which is not necessarily the position
                           held now. Reconstruct it from fill history.
  4. POST IDEMPOTENCY      an order POST that times out may still have been
                           accepted. Query by client id BEFORE resubmitting,
                           or risk a double position.
"""

from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("live.fixes")

# Fix 1 tolerances (STAGE10 §4.1).
BETA_TOLERANCE = 0.15
TRACKING_TOLERANCE = 0.20        # of gross


# --------------------------------------------------------------- fix 1

def book_weights(units: dict[str, float], marks: dict[str, float],
                 equity: float) -> dict[str, float]:
    if not equity:
        return {}
    return {s: (u * marks.get(s, 0.0)) / equity for s, u in units.items() if u}


def tracking_error(target: dict[str, float], actual: dict[str, float]) -> float:
    """Sum of |actual - target| as a fraction of target gross. 0 = perfect."""
    gross = sum(abs(w) for w in target.values())
    if gross <= 0:
        return 0.0
    names = set(target) | set(actual)
    diff = sum(abs(actual.get(s, 0.0) - target.get(s, 0.0)) for s in names)
    return diff / gross


def residual_beta(weights: dict[str, float], betas: dict[str, float]) -> float:
    return float(sum(w * betas.get(s, 0.0) for s, w in weights.items()))


def check_atomicity(target: dict[str, float], actual: dict[str, float],
                    betas: dict[str, float],
                    beta_tol: float = BETA_TOLERANCE,
                    track_tol: float = TRACKING_TOLERANCE) -> dict:
    """STAGE10 §4.1. Returns a verdict describing what the filled book is and
    whether it needs repair. Decides nothing on its own -- the caller acts."""
    te = tracking_error(target, actual)
    rb = residual_beta(actual, betas) if betas else 0.0
    missing = sorted(s for s in target if abs(actual.get(s, 0.0)) < 1e-12)
    extra = sorted(s for s in actual if s not in target and abs(actual[s]) > 1e-12)
    breach = (abs(rb) > beta_tol) or (te > track_tol)
    return {
        "tracking_error": te, "residual_beta": rb,
        "missing_legs": missing, "unexpected_legs": extra,
        "beta_ok": abs(rb) <= beta_tol, "tracking_ok": te <= track_tol,
        "needs_repair": bool(breach),
        "detail": (f"tracking {te:.1%} (tol {track_tol:.0%}), residual beta "
                   f"{rb:+.3f} (tol +/-{beta_tol:.2f})"
                   + (f", missing {missing}" if missing else "")
                   + (f", unexpected {extra}" if extra else "")),
    }


# --------------------------------------------------------------- fix 2

def detect_stop_fills(open_orders_before: list[dict],
                      positions_before: dict[str, float],
                      positions_after: dict[str, float]) -> list[str]:
    """STAGE10 §4.2. Symbols whose position shrank toward zero while a stop was
    working -- i.e. a stop very likely fired.

    Detected from state rather than from a stream event on purpose: a stream
    gap must not become a missed cascade.
    """
    stopped = []
    stop_syms = {o.get("symbol") for o in open_orders_before
                 if str(o.get("type", "")).startswith("STOP")}
    for sym in stop_syms:
        before = positions_before.get(sym, 0.0)
        after = positions_after.get(sym, 0.0)
        if before and abs(after) < abs(before) * 0.5:
            stopped.append(sym)
    return sorted(stopped)


# --------------------------------------------------------------- fix 3

def reconstruct_position_at(fills: list[dict], settlement_ms: int,
                            symbol: str) -> float:
    """STAGE10 §4.3. The signed position in `symbol` at `settlement_ms`,
    rebuilt from fill history.

    `record_day` used to read the CURRENT book, which is wrong whenever a
    rebalance lands near a settlement: funding is charged on what was held at
    the settlement instant. A rebalance ~15s after 00:00 is exactly the case
    that breaks the naive version.

    `fills`: dicts with ts (ms), symbol, side (BUY/SELL), qty.
    """
    pos = 0.0
    for f in sorted(fills, key=lambda x: int(x.get("ts", 0))):
        if f.get("symbol") != symbol:
            continue
        if int(f.get("ts", 0)) > settlement_ms:
            break
        qty = abs(float(f.get("qty", 0.0)))
        pos += qty if str(f.get("side", "")).upper() == "BUY" else -qty
    return pos


def reconcile_funding(recorded: list[dict], exchange_rows: list[dict],
                      tolerance: float = 0.01) -> dict:
    """NOTES 46.2 criterion 2: recorded funding must match the exchange's own
    income history within $0.01 CUMULATIVE."""
    rec = sum(float(r.get("actual_amount", 0.0)) for r in recorded)
    exch = sum(float(r.get("income", 0.0)) for r in exchange_rows)
    drift = rec - exch
    return {
        "recorded": round(rec, 8), "exchange": round(exch, 8),
        "drift": round(drift, 8), "tolerance": tolerance,
        "ok": abs(drift) <= tolerance,
        "n_recorded": len(recorded), "n_exchange": len(exchange_rows),
    }


# --------------------------------------------------------------- fix 4

class AmbiguousPost(RuntimeError):
    """A POST whose outcome is unknown. Never retried blind."""


def place_order_idempotent(client, *, symbol: str, client_order_id: str,
                           max_attempts: int = 3, sleeper=time.sleep,
                           **params) -> dict:
    """STAGE10 §4.4. Place an order; on an ambiguous failure, QUERY BY CLIENT
    ID before deciding whether to resubmit.

    A timeout or 5xx after a POST does not mean the order was rejected -- it
    means the RESPONSE was lost. Blind resubmission is how a book ends up at
    double size. So every attempt goes out under the same `client_order_id`,
    and after any ambiguous failure the order is looked up: if it exists, it is
    returned and nothing is resubmitted.
    """
    from live.client import ExchangeError, FilterRejected, NetworkError

    last: Exception | None = None
    for attempt in range(max_attempts):
        if attempt:
            # Look FIRST. This is the whole fix.
            try:
                existing = client.get_order(symbol, client_order_id)
                if existing:
                    log.warning(
                        "ambiguous POST for %s resolved by query: order %s "
                        "already exists (status %s) -- NOT resubmitting",
                        symbol, client_order_id, existing.get("status"))
                    return existing
            except ExchangeError as e:
                log.info("query-by-client-id for %s found nothing (%s); "
                         "safe to resubmit", client_order_id, e)
            except Exception as e:                       # pragma: no cover
                raise AmbiguousPost(
                    f"could not determine whether {client_order_id} exists: "
                    f"{e}. Refusing to resubmit blind.") from None
            sleeper(min(2.0 ** attempt, 5.0))
        try:
            return client.place_order(symbol=symbol,
                                      newClientOrderId=client_order_id,
                                      **params)
        except FilterRejected:
            raise                    # deterministic: never retried
        except (NetworkError, ExchangeError) as e:
            last = e
            log.warning("order POST for %s failed (attempt %d/%d): %s",
                        symbol, attempt + 1, max_attempts, e)
    raise AmbiguousPost(
        f"order {client_order_id} for {symbol} failed after {max_attempts} "
        f"attempts and no matching order was found: {last}")
