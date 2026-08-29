"""
Stage 10a: the paper-trading dashboard. READ-ONLY, LOCAL, NO KEYS.

The design principle (STAGE10A §0): this is a SECOND WITNESS, not a control
panel. It reads the harness's own files -- what the bot BELIEVES -- rather
than the exchange. When it and Binance's screen disagree, that disagreement is
reconciliation signal, which is the whole point.

Non-negotiables, enforced by test:
  * no exchange client imported anywhere in this package
  * no environment variable containing KEY or SECRET is read
  * no write or control endpoint of any kind -- GET only, and every GET is a
    file read
  * binds 127.0.0.1 by default; remote viewing is an SSH tunnel's job

A dashboard that can trade is an attack surface and an accident surface.
"""

from dashboard.render import render_page  # noqa: F401
from dashboard.server import serve  # noqa: F401

__all__ = ["render_page", "serve"]
