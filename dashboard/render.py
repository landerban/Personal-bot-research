"""
Stage 10a §2: the page. One screen, glanceable, dark, phone-readable.

Pure rendering: takes a status dict (or None) plus log tails and returns HTML.
No I/O, no network, no exchange client, no env vars -- so it is trivially
testable against a healthy / missing / stale / MISMATCH / reset snapshot.

Section order is the spec's priority order and is deliberate: the status strip
answers "is my machine healthy and honest" before anything else is visible.
No market data, no candlesticks, no prices -- Binance already does that.
"""

from __future__ import annotations

import html
import json
from typing import Any

from live.status import STALE_CYCLES, is_stale, staleness

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"

CSS = """
:root{--bg:#0e1116;--panel:#161b22;--line:#2a313c;--fg:#e6edf3;--dim:#8b949e;
--green:#2ea043;--amber:#d29922;--red:#f85149;--accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:980px;margin:0 auto;padding:12px}
h1{font-size:15px;margin:0;letter-spacing:.04em}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.09em;
color:var(--dim);margin:18px 0 6px;font-weight:600}
.strip{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:12px 14px}
.dot{width:14px;height:14px;border-radius:50%;flex:0 0 auto}
.dot.GREEN{background:var(--green);box-shadow:0 0 10px var(--green)}
.dot.AMBER{background:var(--amber);box-shadow:0 0 10px var(--amber)}
.dot.RED{background:var(--red);box-shadow:0 0 10px var(--red)}
.kv{display:flex;flex-direction:column}
.kv .k{color:var(--dim);font-size:11px;text-transform:uppercase;
letter-spacing:.06em}
.kv .v{font-size:15px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:10px 12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:600;font-size:11px;
text-transform:uppercase;letter-spacing:.05em;padding:4px 6px;
border-bottom:1px solid var(--line)}
td{padding:4px 6px;border-bottom:1px solid #1d232c}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--green)}.neg{color:var(--red)}.dim{color:var(--dim)}
.warn{color:var(--amber)}
.gap{color:var(--amber);font-weight:600}
.ok{color:var(--green)}.bad{color:var(--red)}
.empty{color:var(--dim);padding:8px 4px;font-style:italic}
.banner{background:#3d1418;border:1px solid var(--red);color:#ffb4ae;
border-radius:8px;padding:10px 12px;margin-bottom:10px}
.spark{display:block;width:100%;height:64px}
ul.feed{list-style:none;margin:0;padding:0;font-size:12px}
ul.feed li{padding:4px 6px;border-bottom:1px solid #1d232c}
.foot{color:var(--dim);font-size:11px;margin-top:22px;text-align:center}
@media(max-width:560px){.strip{gap:10px}.kv .v{font-size:13px}}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _n(x: Any, nd: int = 2, pct: bool = False, sign: bool = False) -> str:
    if x is None or isinstance(x, str):
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if pct:
        return f"{v * 100:{'+' if sign else ''}.{nd}f}%"
    return f"{v:{'+' if sign else ''}.{nd}f}"


def _cls(x: Any) -> str:
    try:
        return "pos" if float(x) > 0 else "neg" if float(x) < 0 else ""
    except (TypeError, ValueError):
        return ""


def _age(sec: float | None) -> str:
    if sec is None:
        return "—"
    sec = int(sec)
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{sec // 60}m"
    if sec < 172800:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def status_light(snap: dict | None, now: float | None = None) -> tuple[str, str]:
    """(light, reason). RED beats AMBER beats GREEN.

    A stale or missing snapshot is RED, not "unknown": the dashboard must fail
    loud when its source goes quiet, or it becomes a reassurance machine
    (STAGE10A §3).
    """
    if snap is None:
        return RED, "no status.json — harness not reporting"
    if is_stale(snap, now):
        age = staleness(snap, now)
        return RED, (f"status.json stale ({_age(age)} old, limit "
                     f"{STALE_CYCLES} cycles) — harness not reporting")
    if snap.get("halted"):
        return RED, f"HALTED: {snap.get('halt_reason') or 'reason not recorded'}"
    shadow = (snap.get("shadow") or {}).get("result")
    if shadow == "MISMATCH":
        # Stage 10 §3: a mismatch is a same-day stop-and-diagnose. The live
        # path and the research path would be different strategies.
        return RED, "shadow reconciliation MISMATCH — stop and diagnose"
    dd, thr = snap.get("drawdown"), snap.get("kill_switch_threshold") or 0.30
    if dd is not None and float(dd) >= float(thr):
        return RED, f"kill switch: drawdown {_n(dd, pct=True)} ≥ {_n(thr, pct=True)}"
    if not snap.get("kill_switch_armed", False):
        return RED, "kill switch NOT ARMED"

    reasons = []
    hb = snap.get("heartbeat_age_s")
    interval = float(snap.get("cycle_interval_s") or 86_400.0)
    if hb is not None and float(hb) > interval:
        reasons.append(f"heartbeat {_age(hb)} old")
    if snap.get("anomalies"):
        reasons.append(f"{len(snap['anomalies'])} anomaly/ies today")
    guard = snap.get("composition_guard") or {}
    if guard.get("alert"):
        reasons.append(f"composition guard: {guard.get('reason') or 'alert'}")
    if shadow not in ("MATCH", None, "", "n/a"):
        reasons.append(f"shadow {shadow}")
    if reasons:
        return AMBER, "; ".join(reasons)
    return GREEN, "running; all checks passing"


def _sparkline(curve: list) -> str:
    pts = [(float(t), float(v)) for t, v in (curve or [])
           if t is not None and v is not None]
    if len(pts) < 2:
        return '<div class="empty">no equity history yet</div>'
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = (x1 - x0) or 1.0
    dy = (y1 - y0) or 1.0
    coords = " ".join(
        f"{(x - x0) / dx * 100:.2f},{(1 - (y - y0) / dy) * 100:.2f}"
        for x, y in pts
    )
    colour = "var(--green)" if ys[-1] >= ys[0] else "var(--red)"
    return (
        f'<svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none" '
        f'role="img" aria-label="paper equity curve">'
        f'<polyline points="{coords}" fill="none" stroke="{colour}" '
        f'stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>'
        f'<div class="dim">low {y0:,.2f} · high {y1:,.2f}</div>'
    )


def _positions(snap: dict) -> str:
    rows = snap.get("positions") or []
    if not rows:
        return '<div class="empty">flat — no open positions</div>'
    out = ["<table><tr><th>symbol</th><th>side</th><th class='num'>notional</th>"
           "<th class='num'>entry→mark</th><th class='num'>uPnL</th>"
           "<th class='num'>target w</th><th class='num'>actual w</th>"
           "<th class='num'>gap</th></tr>"]
    for p in rows:
        tw, aw = p.get("target_weight"), p.get("actual_weight")
        gap = None
        if tw is not None and aw is not None:
            gap = float(aw) - float(tw)
        gap_cls = "gap" if (gap is not None and abs(gap) > 1e-6) else "dim"
        out.append(
            f"<tr><td>{_e(p.get('symbol'))}</td><td>{_e(p.get('side'))}</td>"
            f"<td class='num'>{_n(p.get('notional'))}</td>"
            f"<td class='num dim'>{_n(p.get('entry'), 4)}→{_n(p.get('mark'), 4)}</td>"
            f"<td class='num {_cls(p.get('upnl'))}'>{_n(p.get('upnl'), 2, sign=True)}</td>"
            f"<td class='num'>{_n(tw, 4, sign=True)}</td>"
            f"<td class='num'>{_n(aw, 4, sign=True)}</td>"
            f"<td class='num {gap_cls}'>{_n(gap, 4, sign=True)}</td></tr>")
    out.append("</table>")
    return "".join(out)


CRITERIA_LABELS = [
    ("shadow_reconciliation", "1 · shadow reconciliation matched every day"),
    ("funding_reconciles", "2 · funding reconciles to exchange income (≤ $0.01)"),
    ("no_unrecovered_crash", "3 · no unrecovered crash (incl. induced kill)"),
    ("phase2_fixes", "4 · all four Phase-2 fixes demonstrated"),
    ("killswitch_watchdog", "5 · kill switch + watchdog verified armed"),
    ("zero_silent_errors", "6 · zero silent errors"),
]


def _criteria(snap: dict) -> str:
    c = snap.get("criteria") or {}
    if not c:
        return '<div class="empty">no criteria data yet</div>'
    out = ["<table>"]
    for key, label in CRITERIA_LABELS:
        v = c.get(key)
        if isinstance(v, dict):
            ok, detail = v.get("ok"), v.get("detail", "")
        else:
            ok, detail = v, ""
        mark = "PASS" if ok is True else "FAIL" if ok is False else "—"
        cls = "ok" if ok is True else "bad" if ok is False else "dim"
        out.append(f"<tr><td>{_e(label)}</td>"
                   f"<td class='num {cls}'>{mark}</td>"
                   f"<td class='dim'>{_e(detail)}</td></tr>")
    out.append("</table>")
    return "".join(out)


def _guard(snap: dict) -> str:
    g = snap.get("composition_guard") or {}
    if not g:
        return '<div class="empty">no composition-guard data yet</div>'
    cls = "warn" if g.get("alert") else "dim"
    amb = g.get("ambiguous") or []
    ex = g.get("excluded") or []
    bits = [
        f"<div class='{cls}'>excluded today: <b>{len(ex)}</b>"
        f" · ambiguous: <b>{len(amb)}</b>"
        f" · in unfiltered top-15: <b>{g.get('excluded_in_top15', '—')}</b></div>"
    ]
    if g.get("alert"):
        bits.append(f"<div class='warn'>ALERT: {_e(g.get('reason'))}</div>")
    if ex:
        bits.append("<ul class='feed'>" + "".join(
            f"<li>{_e(e.get('symbol') if isinstance(e, dict) else e)}"
            f" <span class='dim'>{_e(e.get('reason') if isinstance(e, dict) else '')}</span></li>"
            for e in ex[:12]) + "</ul>")
    return "".join(bits)


def _fills(snap: dict) -> str:
    fills = snap.get("fills_today") or []
    skips = snap.get("skips") or {}
    funding = snap.get("funding_today") or []
    out = []
    if fills:
        out.append("<table><tr><th>symbol</th><th>side</th><th class='num'>qty</th>"
                   "<th class='num'>fill</th><th class='num'>slip bps</th>"
                   "<th class='num'>fee</th></tr>")
        for f in fills[:30]:
            out.append(
                f"<tr><td>{_e(f.get('symbol'))}</td><td>{_e(f.get('side'))}</td>"
                f"<td class='num'>{_n(f.get('qty'), 4)}</td>"
                f"<td class='num'>{_n(f.get('fill_price'), 4)}</td>"
                f"<td class='num'>{_n(f.get('slippage_bps'), 1, sign=True)}</td>"
                f"<td class='num'>{_n(f.get('fee'), 4)}</td></tr>")
        out.append("</table>")
    else:
        out.append('<div class="empty">no fills today</div>')
    if skips:
        out.append("<div class='dim'>skips: " + ", ".join(
            f"{_e(k)}×{v}" for k, v in sorted(skips.items(), key=lambda kv: -kv[1])
        ) + "</div>")
    if funding:
        tot = sum(float(f.get("amount") or 0) for f in funding)
        out.append(f"<div class='dim'>funding settlements recorded: "
                   f"{len(funding)} · net {_n(tot, 4, sign=True)}</div>")
    return "".join(out)


def _anomalies(snap: dict) -> str:
    """Newest first. NOTHING is filtered out of this feed (STAGE10A §2.6)."""
    rows = snap.get("anomalies") or []
    if not rows:
        return '<div class="empty">none — which is the goal</div>'
    out = ["<ul class='feed'>"]
    for a in rows[:50]:
        if isinstance(a, dict):
            when = a.get("ts") or a.get("time") or ""
            msg = a.get("msg") or a.get("error") or json.dumps(a, default=str)
        else:
            when, msg = "", str(a)
        out.append(f"<li><span class='dim'>{_e(when)}</span> {_e(msg)}</li>")
    out.append("</ul>")
    return "".join(out)


def render_page(snap: dict | None, now: float | None = None,
                refresh_s: int = 45) -> str:
    light, reason = status_light(snap, now)
    s = snap or {}

    banner = ""
    if light == RED:
        banner = f'<div class="banner"><b>RED</b> — {_e(reason)}</div>'

    dd = s.get("drawdown")
    thr = s.get("kill_switch_threshold") or 0.30
    ks = (f"DD {_n(dd, 1, pct=True)} of {_n(thr, 0, pct=True)}"
          if dd is not None else "—")
    resets = s.get("testnet_resets") or []

    strip = f"""
<div class="strip">
  <div class="dot {light}" title="{_e(reason)}"></div>
  <div class="kv"><span class="k">status</span><span class="v">{light}</span></div>
  <div class="kv"><span class="k">heartbeat</span>
    <span class="v">{_age(s.get('heartbeat_age_s'))}</span></div>
  <div class="kv"><span class="k">day</span>
    <span class="v">{_e(s.get('day_counter', 0))} of {_e(s.get('day_target', 28))}</span></div>
  <div class="kv"><span class="k">kill switch</span><span class="v">{ks}</span></div>
  <div class="kv"><span class="k">snapshot age</span>
    <span class="v">{_age(staleness(s, now))}</span></div>
</div>
<div class="dim" style="padding:6px 2px">{_e(reason)}</div>"""

    equity = f"""
<div class="panel">
  <div class="strip" style="border:0;background:transparent;padding:0 0 8px">
    <div class="kv"><span class="k">paper equity</span>
      <span class="v">{_n(s.get('equity'))}</span></div>
    <div class="kv"><span class="k">today</span>
      <span class="v {_cls(s.get('day_pnl'))}">{_n(s.get('day_pnl'), 2, sign=True)}</span></div>
    <div class="kv"><span class="k">cum price</span>
      <span class="v {_cls(s.get('cum_price_pnl'))}">{_n(s.get('cum_price_pnl'), 2, sign=True)}</span></div>
    <div class="kv"><span class="k">cum funding</span>
      <span class="v {_cls(s.get('cum_funding_pnl'))}">{_n(s.get('cum_funding_pnl'), 2, sign=True)}</span></div>
    <div class="kv"><span class="k">exchange bal</span>
      <span class="v dim">{_n(s.get('exchange_balance'))}</span></div>
  </div>
  {_sparkline(s.get('equity_curve'))}
  {'<div class="warn">' + str(len(resets)) + ' testnet reset(s) — series re-baselined, kill switch not fired</div>' if resets else ''}
  <div class="dim">PnL is NOT a success criterion (NOTES 46.1): testnet PnL is noise.</div>
</div>"""

    book = f"""
<div class="panel">{_positions(s)}
  <div class="dim" style="margin-top:6px">gross leverage
    {_n(s.get('gross_leverage'))} · realised beta {_n(s.get('realised_beta'), 3, sign=True)}</div>
</div>"""

    shadow = s.get("shadow") or {}
    shadow_cls = ("ok" if shadow.get("result") == "MATCH"
                  else "bad" if shadow.get("result") == "MISMATCH" else "dim")

    body = f"""<div class="wrap">
<h1>xsmom · paper (testnet) · read-only</h1>
{banner}
{strip}
<h2>equity</h2>{equity}
<h2>book</h2>{book}
<h2>shadow reconciliation</h2>
<div class="panel"><span class="{shadow_cls}">{_e(shadow.get('result') or 'no data yet')}</span>
  <span class="dim"> {_e(shadow.get('detail') or '')}</span>
  <div class="dim">max weight delta {_n(shadow.get('max_weight_delta'), 9)} (tolerance 1e-6)</div></div>
<h2>the six §46 criteria</h2><div class="panel">{_criteria(s)}</div>
<h2>composition guard (§48.6)</h2><div class="panel">{_guard(s)}</div>
<h2>today</h2><div class="panel">{_fills(s)}</div>
<h2>anomalies — unfiltered</h2><div class="panel">{_anomalies(s)}</div>
<div class="foot">read-only · no keys · no exchange client · 127.0.0.1 ·
refreshes every {refresh_s}s</div>
</div>"""

    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="{refresh_s}">'
            f'<title>xsmom paper · {light}</title><style>{CSS}</style></head>'
            f'<body>{body}</body></html>')
