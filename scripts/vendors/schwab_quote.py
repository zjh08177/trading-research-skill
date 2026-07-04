"""Schwab live-quote CLI: emits P1 current-price facts the settled-bars path can't.

`P1.last` is the day-D price (last trade, its own trade-time as-of) that closes
the "query on day D returns day D-1" gap. Prior close and chg% still come from
`schwab_bars.py` (settled) — never this quote. `P1.is_realtime` lets the caller
box the price DELAYED when entitlement lapses.
"""
import argparse
import datetime
import sys

from _common import die, emit, fact

from tradingagents.dataflows import schwab
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

SRC = "schwab"


def build_facts(env):
    """P1 current-price facts; day range/volume omitted (never null) when absent."""
    q = env.data
    # vendor_as_of is the real trade-time — guaranteed non-None (fetch fails loud
    # otherwise), so the price is never stamped with a fabricated now().
    asof = env.provenance.vendor_as_of.isoformat()
    facts = {"P1.last": fact(q.last, "USD", asof, SRC)}
    if q.day_high is not None:
        facts["P1.day_high"] = fact(q.day_high, "USD", asof, SRC)
    if q.day_low is not None:
        facts["P1.day_low"] = fact(q.day_low, "USD", asof, SRC)
    if q.day_volume is not None:
        facts["P1.day_volume"] = fact(q.day_volume, "shares", asof, SRC)
    facts["P1.is_realtime"] = fact(q.is_realtime, "bool", asof, SRC)
    return facts


def main(argv):
    p = argparse.ArgumentParser(prog="schwab_quote")
    p.add_argument("--ticker", required=True)
    # A quote is a live snapshot with no history, so it is valid ONLY for a
    # current-day run. A past as_of must use settled bars (else look-ahead); a
    # future as_of has no live datum. Compare parsed dates (not strings) against
    # the local calendar date — the basis the orchestrator's as_of is built on
    # and the one tiingo_oracle uses — so both P1 sources gate identically.
    p.add_argument("--asof", default=None)
    args = p.parse_args(argv)
    if args.asof is not None:
        try:
            asof = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            die("invalid --asof %r (expected YYYY-MM-DD)" % args.asof, 2)
        if asof != datetime.date.today():
            die("live quote only valid for a current-day as_of (got %s); use settled bars" % args.asof, 3)
    try:
        env = schwab.SchwabQuoteVendor.fetch(args.ticker)
    except VendorNotConfiguredError as e:  # incl. SchwabReauthRequiredError; before ValueError
        die(str(e), 2)
    except NoMarketDataError as e:
        die(str(e), 3)
    except VendorRateLimitError as e:
        die(str(e), 4)
    except Exception as e:
        die("%s: %s" % (type(e).__name__, e), 1)
    emit(build_facts(env))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
