"""Historical as-of replay contract helpers. Pure, no network access.

Owns the mechanical point-in-time contract shared by build_datapack.py,
qa_check.py, and marketaux_news.py:

    parse_cutoff_token(token, today=None) -> date
    mode_for_cutoff(cutoff, today=None) -> "live" | "replay"
    write_scope(run_dir, meta) -> (scope_json_path, scope_md_path)
    check_pack_cutoff(pack, cutoff, replay=False, position_pack=None) -> (errors, warnings)
    filter_headlines_for_replay(rows, information_cutoff) -> (accepted_rows, gap_messages)
"""
import datetime as dt
import json
import os
import re

ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
SLASH_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")

# Facts forbidden from any replay pack/position-pack (ERD R3/R8/R9).
FORBIDDEN_EXACT = ("P1.last",)
FORBIDDEN_PREFIXES = ("H1.", "P4.", "P8.")


class CutoffError(ValueError):
    """Malformed or future cutoff token (grammar or look-ahead violation)."""


def parse_cutoff_token(token, today=None):
    """Accept ONLY zero-padded YYYY-MM-DD or YYYY/MM/DD; normalize to a date.

    Rejects future dates (relative to `today`, default real today) so a
    look-ahead cutoff can never reach a vendor call.
    """
    today = today or dt.date.today()
    if not isinstance(token, str):
        raise CutoffError(f"cutoff must be a string, got {type(token).__name__}")
    m = ISO_RE.match(token) or SLASH_RE.match(token)
    if not m:
        raise CutoffError(
            f"invalid cutoff token {token!r}: expected zero-padded YYYY-MM-DD or YYYY/MM/DD"
        )
    y, mo, d = (int(x) for x in m.groups())
    try:
        cutoff = dt.date(y, mo, d)
    except ValueError as e:
        raise CutoffError(f"invalid cutoff token {token!r}: {e}") from e
    if cutoff > today:
        raise CutoffError(
            f"cutoff {cutoff.isoformat()} is in the future (today {today.isoformat()})"
        )
    return cutoff


def mode_for_cutoff(cutoff, today=None):
    """Today's date -> live; any strictly-past date -> replay."""
    today = today or dt.date.today()
    return "live" if cutoff == today else "replay"


def write_scope(run_dir, meta):
    """Write 00-scope.json + 00-scope.md from a metadata dict.

    `meta` keys of interest: mode, ticker, requested_cutoff,
    effective_market_asof, entry_market_asof, generated_at, asset_class,
    conservative_fallback.
    """
    os.makedirs(run_dir, exist_ok=True)
    scope = dict(meta)
    json_path = os.path.join(run_dir, "00-scope.json")
    with open(json_path, "w") as f:
        json.dump(scope, f, indent=1)
        f.write("\n")
    mode = scope.get("mode", "live")
    lines = ["# Scope", ""]
    if mode == "replay":
        lines.append("**Historical replay**")
        lines.append("")
    lines.append(f"- Mode: {mode}")
    lines.append(f"- Ticker: {scope.get('ticker')}")
    if scope.get("asset_class"):
        lines.append(f"- Asset class: {scope['asset_class']}")
    if scope.get("requested_cutoff"):
        lines.append(f"- Requested cutoff: {scope['requested_cutoff']}")
    if scope.get("effective_market_asof"):
        lines.append(f"- Effective market as-of: {scope['effective_market_asof']}")
    if scope.get("entry_market_asof"):
        lines.append(f"- Entry market as-of: {scope['entry_market_asof']}")
    if scope.get("conservative_fallback"):
        lines.append(
            "- Conservative fallback: entry market as-of capped to effective "
            "market as-of (first settled close after cutoff unavailable)"
        )
    lines.append(f"- Generated at: {scope.get('generated_at')}")
    md_path = os.path.join(run_dir, "00-scope.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return json_path, md_path


def _date_prefix(value):
    """Parse the first 10 chars of a string as an ISO date; None if invalid."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _as_date(cutoff):
    if isinstance(cutoff, dt.date):
        return cutoff
    d = _date_prefix(cutoff)
    if d is None:
        raise CutoffError(f"invalid cutoff value: {cutoff!r}")
    return d


def _is_forbidden_key(key):
    return key in FORBIDDEN_EXACT or any(key.startswith(p) for p in FORBIDDEN_PREFIXES)


def check_pack_cutoff(pack, cutoff, replay=False, position_pack=None):
    """Scan a datapack (+ optional position pack) for look-ahead / forbidden
    replay facts. Returns (errors, warnings); errors are always hard failures.
    """
    cutoff = _as_date(cutoff)
    pack = pack or {}
    position_pack = position_pack or {}
    errors, warnings = [], []

    if replay:
        for key in list(pack.keys()) + list(position_pack.keys()):
            if _is_forbidden_key(key):
                errors.append(f"forbidden replay fact present: {key}")

    for key, f in pack.items():
        if not isinstance(f, dict):
            continue
        asof = f.get("asof")
        if isinstance(asof, str):
            d = _date_prefix(asof)
            if d is not None and d > cutoff:
                errors.append(f"{key}: asof {asof} is after cutoff {cutoff.isoformat()}")
        known_at = f.get("known_at")
        known_at_date = None
        if known_at is not None:
            known_at_date = _date_prefix(known_at)
            if known_at_date is not None and known_at_date > cutoff:
                errors.append(
                    f"{key}: known_at {known_at} is after cutoff {cutoff.isoformat()}"
                )
        v = f.get("v")
        date_valued = f.get("unit") == "date" or key.endswith("_filed") or key.endswith("_earnings")
        if date_valued and isinstance(v, str):
            d = _date_prefix(v)
            if d is not None and d > cutoff:
                covered = known_at_date is not None and known_at_date <= cutoff
                if not covered:
                    errors.append(
                        f"{key}: value {v} is a future date not covered by known_at <= cutoff"
                    )
        if replay and key.startswith("P3.") and f.get("src") == "sec-edgar" and known_at is None:
            errors.append(f"{key}: replay P3 fact from sec-edgar is missing known_at")

    headlines = pack.get("P5.headlines")
    if replay and isinstance(headlines, dict):
        rows = headlines.get("v")
        if isinstance(rows, list):
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                pub = row.get("published_at")
                d = _date_prefix(pub) if isinstance(pub, str) else None
                if d is None:
                    errors.append(f"P5.headlines[{i}]: missing/unparseable published_at")
                elif d > cutoff:
                    errors.append(
                        f"P5.headlines[{i}]: published_at {pub} is after cutoff {cutoff.isoformat()}"
                    )

    next_earnings = pack.get("P5.next_earnings")
    if replay and isinstance(next_earnings, dict):
        ne_known_at = next_earnings.get("known_at")
        d = _date_prefix(ne_known_at) if isinstance(ne_known_at, str) else None
        if d is None or d > cutoff:
            errors.append("P5.next_earnings: forbidden in replay without known_at <= cutoff")

    return errors, warnings


def filter_headlines_for_replay(rows, information_cutoff):
    """Reject headline rows with missing/unparseable/after-cutoff published_at.

    Returns (accepted_rows, gap_messages); gap_messages name the rejected
    source so build_datapack can surface it in Data Gaps.
    """
    cutoff = _as_date(information_cutoff)
    accepted, gaps = [], []
    for row in rows or []:
        label = None
        pub = None
        if isinstance(row, dict):
            pub = row.get("published_at")
            label = row.get("title") or row.get("source") or row.get("url")
        label = label or "headline"
        d = _date_prefix(pub) if isinstance(pub, str) else None
        if d is None:
            gaps.append(f"P5 headline excluded (missing/unparseable published_at): {label}")
            continue
        if d > cutoff:
            gaps.append(
                f"P5 headline excluded (published {pub} after cutoff {cutoff.isoformat()}): {label}"
            )
            continue
        accepted.append(row)
    return accepted, gaps
