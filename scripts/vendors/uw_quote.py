"""Unusual Whales live-quote CLI: emits P1 current-price facts — the UW-sourced
replacement for ``schwab_quote.py``.

Reads UW ``/api/stock/{ticker}/stock-state``, whose ``close`` is the current/last
price (during the session), with ``high``/``low``/``total_volume`` for the day and
``tape_time`` as the real quote time. ``P1.last`` is stamped with ``tape_time`` —
never a fabricated ``now()`` — so an as-of that precedes the quote can be boxed.

``P1.is_realtime`` is derived from ``tape_time`` freshness: verified live during
market hours the UW tape runs ~2-3s behind wall-clock and matches Schwab's
real-time NBBO / Tiingo IEX to <0.05%, so a tape within ``FRESH_WINDOW`` of now is
real-time. A tape older than that (a hypothetical UW delay, or an after-hours /
closed-market last trade) reports ``False`` so the writer boxes the headline
``DELAYED``/``STALE`` per invariant 11 rather than over-claiming a live price.

Like ``schwab_quote``, a quote is a live snapshot with no history: valid ONLY for
a current-day ``--asof``. A past as_of must use settled bars (else look-ahead); a
future as_of has no live datum. Exit 3 in both cases.

Exit codes: 0 ok, 2 auth/config (missing key / 401 / bad --asof), 3 no data
(404 / empty / non-today as_of), 4 rate-limit (429), 1 other.
"""
import argparse
import datetime

import _uw_common as uw

SRC = "uw"
# A UW tape within this many seconds of now is treated as real-time. Sized well
# above the observed ~2-3s live lag but far below a 15-minute delayed feed, so a
# genuinely delayed or after-hours/closed tape falls outside it and boxes DELAYED.
FRESH_WINDOW_S = 120


def build_facts(state):
    """P1 current-price facts from a stock-state dict. Day range/volume omitted
    (never null) when a field is absent. ``asof`` is the vendor tape time."""
    # The as-of MUST be the vendor's real tape time, never now(): fabricating it
    # would make a stale response look freshly quoted and defeat the STALE/DELAYED
    # boxing in invariant 11. A response with no tape_time is malformed -> fail.
    asof = state.get("tape_time")
    if not asof:
        uw.die("UW stock-state has no tape_time (cannot stamp a real quote time)", 3)

    def num(key):
        try:
            return float(state[key])
        except (KeyError, TypeError, ValueError):
            return None

    last = num("close")
    if last is None:
        uw.die("UW stock-state has no usable last price", 3)
    facts = {"P1.last": uw.fact(last, "USD", asof, SRC)}
    dh, dl = num("high"), num("low")
    if dh is not None:
        facts["P1.day_high"] = uw.fact(dh, "USD", asof, SRC)
    if dl is not None:
        facts["P1.day_low"] = uw.fact(dl, "USD", asof, SRC)
    dv = num("total_volume")
    if dv is not None:
        facts["P1.day_volume"] = uw.fact(dv, "shares", asof, SRC)
    facts["P1.is_realtime"] = uw.fact(_is_fresh(asof), "bool", asof, SRC)
    return facts


def _is_fresh(tape_time, now=None):
    """True when the vendor tape is within FRESH_WINDOW_S of now (real-time).

    Uses now() only to MEASURE staleness — never to stamp provenance. An
    unparseable tape time is treated as not-fresh (fail safe to DELAYED)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        t = datetime.datetime.fromisoformat(str(tape_time).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return abs((now - t).total_seconds()) <= FRESH_WINDOW_S


def fetch_state(ticker):
    status, body = uw.get_json("/api/stock/%s/stock-state" % ticker)
    if status == 401:
        uw.die("UW auth failed (401): check key in %s" % uw.CREDS_PATH, 2)
    if status == 429:
        uw.die("UW rate limited (429) on stock-state/%s" % ticker, 4)
    if status == 404:
        uw.die("UW no data (404) for %s" % ticker, 3)
    if status != 200:
        uw.die("UW HTTP %s on stock-state/%s: %s" % (status, ticker, str(body)[:160]), 1)
    state = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(state, dict) or not state:
        uw.die("UW malformed/empty stock-state for %s" % ticker, 3)
    return state


def main(argv):
    p = argparse.ArgumentParser(prog="uw_quote")
    p.add_argument("--ticker", required=True)
    # Gate identically to schwab_quote: live quote is current-day only. Compare
    # parsed dates against the local calendar date so both P1 sources gate alike.
    p.add_argument("--asof", default=None)
    args = p.parse_args(argv)
    if args.asof is not None:
        try:
            asof = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            uw.die("invalid --asof %r (expected YYYY-MM-DD)" % args.asof, 2)
        if asof != datetime.date.today():
            uw.die(
                "live quote only valid for a current-day as_of (got %s); use settled bars"
                % args.asof,
                3,
            )
    state = fetch_state(args.ticker.upper())
    uw.emit(build_facts(state))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
