"""Schwab account CLI: emits H1 position facts for the run's ticker (read-only).

Aggregates the user's holding in ``--ticker`` across all Schwab accounts. The
emitted artifact is withheld from analysts/debate/risk/judges (invariant 12) and
read only by the writer and qa_check. Live-only: a past/future ``--asof`` yields
no position (exit 3). Read-only: GET /accounts only — no order/trade endpoint
(invariant 13).
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


def build_facts(pos, asof):
    """H1 facts for the ticker. Flat position → a single positive H1.held=false."""
    if pos is None:
        return {"H1.held": fact(False, "bool", asof, SRC)}
    return {
        "H1.held": fact(True, "bool", asof, SRC),
        "H1.qty": fact(pos.qty, "shares", asof, SRC),
        "H1.avg_price": fact(pos.avg_price, "USD", asof, SRC),
        "H1.market_value": fact(pos.market_value, "USD", asof, SRC),
        "H1.unrealized_pl": fact(pos.unrealized_pl, "USD", asof, SRC),
        "H1.unrealized_pl_pct": fact(pos.unrealized_pl_pct, "%", asof, SRC),
        "H1.pct_of_book": fact(pos.pct_of_book, "%", asof, SRC),
        "H1.n_accounts": fact(pos.n_accounts, "count", asof, SRC),
    }


def main(argv):
    p = argparse.ArgumentParser(prog="schwab_account")
    p.add_argument("--ticker", required=True)
    # Positions are a live snapshot, valid only for a current-day run. A past
    # as_of would need historical holdings (unavailable); a future one has none.
    # Compare parsed dates against the local calendar date, like schwab_quote.
    p.add_argument("--asof", default=None)
    args = p.parse_args(argv)
    if args.asof is not None:
        try:
            asof = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            die("invalid --asof %r (expected YYYY-MM-DD)" % args.asof, 2)
        if asof != datetime.date.today():
            die("account positions are live-only (got %s); back-dated runs carry no position" % args.asof, 3)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        pos = schwab.SchwabAccountVendor.fetch_position(args.ticker)
    except VendorNotConfiguredError as e:  # incl. SchwabReauthRequiredError; before ValueError
        die(str(e), 2)
    except NoMarketDataError as e:
        die(str(e), 3)
    except VendorRateLimitError as e:
        die(str(e), 4)
    except Exception as e:
        die("%s: %s" % (type(e).__name__, e), 1)
    emit(build_facts(pos, stamp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
