"""UW options-depth (P8) distiller (R8, tech-solution §4.3).

Pure derive-only, like every distiller in this package: `raw_rows` is the
P8.* fact dict already built by scripts/vendors/uw_options.build() (the
fct() shape: v/unit/asof/src[+extras]) -- NEVER raw UW vendor JSON (R1).
"""
from ._base import signal

_GAPS_KEY = "P8._gaps"


def _direction_from_flow(rows):
    """rows: [[type, strike, expiry, volume], ...]. Sums volume by put/call
    side (matched case-insensitively on the `type` string) and returns a
    directional label. Ties or unmatched rows -> 'neutral'."""
    put_vol = 0.0
    call_vol = 0.0
    for row in rows or []:
        if not row:
            continue
        kind = str(row[0] or "").lower()
        vol = row[3] if len(row) > 3 else None
        try:
            vol = float(vol) if vol is not None else 0.0
        except (TypeError, ValueError):
            vol = 0.0
        if "put" in kind:
            put_vol += vol
        elif "call" in kind:
            call_vol += vol
    if put_vol == call_vol:
        return "neutral"
    return "bearish" if put_vol > call_vol else "bullish"


def _trend_from_series(rows):
    """rows: [[date, net_gex], ...], chronological oldest-first (uw_options
    convention). Compares the mean of the first half to the second half; a
    move under 1% of scale is 'flat'. Runs over the FULL series, before any
    salience cap, so the label reflects real history, not a truncated tail."""
    vals = [r[1] for r in (rows or []) if r and len(r) > 1 and r[1] is not None]
    if len(vals) < 2:
        return "flat"
    mid = len(vals) // 2 or 1
    first_half = vals[:mid]
    second_half = vals[mid:] or vals[-1:]
    a = sum(first_half) / len(first_half)
    b = sum(second_half) / len(second_half)
    scale = max(abs(a), abs(b), 1.0)
    if abs(b - a) / scale < 0.01:
        return "flat"
    return "rising" if b > a else "falling"


def _oi_change_agg(rows):
    """rows: [[expiry, oi, volume], ...]. Aggregate stand-in for the capped
    oi_walls list (list-cite guardrail): total day volume across expiries
    (today's OI-moving flow), so a report can cite one scalar (P8.*_agg)
    instead of the raw per-expiry table."""
    total = 0.0
    for row in rows or []:
        if not row or len(row) < 3 or row[2] is None:
            continue
        try:
            total += float(row[2])
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def _cap(fid, fact, max_rows):
    """Cap any unit=='list' fact to max_rows rows (unit-driven, not
    key-enumerated -- N1), naming the omission (R3) so truncation is never
    silent. Keeps the tail: most raw_rows lists are chronological
    (freshest last) or already salience-presorted upstream."""
    rows = fact.get("v") or []
    total = len(rows)
    kept = rows[-max_rows:] if total > max_rows else rows
    gap = f"kept {max_rows} of {total} rows; see {fid}" if total > max_rows else None
    return signal(fid, kept, fact.get("unit"), fact.get("asof"),
                  fact.get("src"), gap=gap)


def distill(raw_rows, ctx) -> list:
    raw_rows = raw_rows or {}
    facts = {k: v for k, v in raw_rows.items()
             if k != _GAPS_KEY and isinstance(v, dict)}
    upstream_gaps = raw_rows.get(_GAPS_KEY) or []

    if not facts:
        signals = [signal(
            "P8.options_quiet", "quiet: no options data available",
            "none", ctx.asof, "uw", notable=False,
        )]
        for msg in upstream_gaps:
            signals.append(signal(_GAPS_KEY, None, "none", ctx.asof, "uw", gap=msg))
        return signals

    signals = []

    flow_fact = facts.get("P8.flow_alerts")
    if flow_fact and flow_fact.get("v"):
        signals.append(signal(
            "P8.flow_direction", _direction_from_flow(flow_fact["v"]), "label",
            flow_fact.get("asof") or ctx.asof, "uw",
        ))

    series_fact = facts.get("P8.gex_series")
    series_rows = (series_fact or {}).get("v") or []
    if len(series_rows) >= 2:
        signals.append(signal(
            "P8.gex_series_trend", _trend_from_series(series_rows), "label",
            series_fact.get("asof") or ctx.asof, "uw",
        ))

    oi_fact = facts.get("P8.oi_walls")
    if oi_fact and oi_fact.get("v"):
        signals.append(signal(
            "P8.oi_change_agg", _oi_change_agg(oi_fact["v"]), "count",
            oi_fact.get("asof") or ctx.asof, "uw",
        ))

    for fid, fact in facts.items():
        if fact.get("unit") == "list":
            signals.append(_cap(fid, fact, ctx.max_rows))
        else:
            signals.append(signal(
                fid, fact.get("v"), fact.get("unit"), fact.get("asof"),
                fact.get("src") or "uw",
            ))

    for msg in upstream_gaps:
        signals.append(signal(_GAPS_KEY, None, "none", ctx.asof, "uw", gap=msg))

    return signals
