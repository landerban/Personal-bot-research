"""
Stage 10a §3: the serving layer. stdlib only, read-only, 127.0.0.1.

WHY stdlib INSTEAD OF FastAPI/Flask
-----------------------------------
STAGE10A §3 says "one small FastAPI (or Flask) app ... no build step, no
framework, no database -- it reads files". Neither package is installed here,
and adding a web-framework dependency to a project whose runtime deps are
numpy and requests, in order to serve one read-only local page, works against
that same constraint. `http.server` satisfies every non-negotiable -- one
page, no build step, file reads only -- and adds nothing to install.

The handler implements GET only. There is no POST/PUT/DELETE method on it at
all, so a write endpoint cannot be reached even by accident: the base class
answers 501 for any verb it has no do_<VERB> for.
"""

from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dashboard.render import render_page, status_light
from live.status import read_status

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / "live" / "state" / "status.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


class DashboardHandler(BaseHTTPRequestHandler):
    """GET only. Every response is derived from a file the harness wrote."""

    status_path: Path = DEFAULT_STATUS
    refresh_s: int = 45
    server_version = "xsmom-dashboard"
    sys_version = ""

    def log_message(self, fmt, *args):  # keep the console for the harness
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # It reads local files and renders them; nothing external is fetched.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; "
                         "img-src data:")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        snap = read_status(self.status_path)
        if path in ("/", "/index.html"):
            html = render_page(snap, refresh_s=self.refresh_s)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/health":
            light, reason = status_light(snap)
            code = 200 if light != "RED" else 503
            self._send(code, f"{light} {reason}\n".encode("utf-8"),
                       "text/plain; charset=utf-8")
        else:
            self._send(404, b"not found\n", "text/plain; charset=utf-8")


def serve(status_path: Path | str = DEFAULT_STATUS, host: str = DEFAULT_HOST,
          port: int = DEFAULT_PORT, refresh_s: int = 45,
          server_class=ThreadingHTTPServer) -> ThreadingHTTPServer:
    """Build (do not start) the server. The caller runs serve_forever, so
    tests can drive it on an ephemeral port and shut it down cleanly."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        # STAGE10A §0: remote viewing is an SSH tunnel or Tailscale, never a
        # bind to the world. Refused rather than warned about.
        raise ValueError(
            f"refusing to bind {host!r}: the dashboard binds loopback only. "
            f"For remote viewing use an SSH tunnel, not a wider bind."
        )
    handler = type("BoundHandler", (DashboardHandler,), {
        "status_path": Path(status_path), "refresh_s": refresh_s,
    })
    return server_class((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dashboard")
    ap.add_argument("--status", default=str(DEFAULT_STATUS))
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--refresh", type=int, default=45)
    a = ap.parse_args(argv)

    httpd = serve(a.status, a.host, a.port, a.refresh)
    p = Path(a.status)
    print(f"xsmom paper dashboard  http://{a.host}:{a.port}")
    print(f"  reading  {p}")
    print(f"  {'FOUND' if p.exists() else 'ABSENT (page will show RED)'}"
          f" · refresh {a.refresh}s · read-only, no keys")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
