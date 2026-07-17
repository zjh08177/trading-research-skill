"""Unusual Whales live-quote CLI: emits P1 current-price facts — the UW-sourced
replacement for ``schwab_quote.py``.

Reads UW ``/api/stock/{ticker}/stock-state``, whose ``close`` is the current/last
price (during the session), with ``high``/``low``/``total_volume`` for the day and
``tape_time`` as the real quote time. ``P1.last`` is stamped with ``tape_time`` —
never a fabricated ``now()`` — so an as-of that precedes the quote can be boxed.

``P1.is_realtime`` is emitted **False** deliberately: UW's REST real-time
entitlement is not yet independently verified (the intraday side-by-side vs a
known real-time feed is a pending follow-up), so the writer boxes the headline
``DELAYED`` per invariant 11 rather than over-claiming live pricing. Flip to a
``tape_time``-freshness derivation only once that verification is done.

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


def build_facts(state):
    """P1 current-price facts from a stock-state dict. Day range/volume omitted
    (never null) when a field is absent. ``asof`` is the vendor tape time."""
    asof = state.get("tape_time") or datetime.datetime.now(datetime.timezone.utc).isoformat()

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
    # Real-time entitlement unverified -> False -> writer boxes DELAYED (inv 11).
    facts["P1.is_realtime"] = uw.fact(False, "bool", asof, SRC)
    return facts


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
