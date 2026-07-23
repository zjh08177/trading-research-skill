"""Finnhub out-of-band price oracle: the THIRD independent P1 cross-check
source, fetched only when the primary live quote (`uw_quote.py`'s P1.last)
and tiingo (existing oracle) already disagree beyond tolerance — see
scripts/price_crosscheck.py, which resolves the 2-of-3 vote. Current-day
only: Finnhub /quote has no as-of parameter, it always returns the
live/last-session price, so a past/future --asof is refused rather than
silently mislabeled (never look-ahead).

Emits P1.px_finnhub_oob. Best-effort by design (same posture as
tiingo_oracle.py --live): callers treat a nonzero exit as "3rd source
unavailable", not a pipeline failure.
"""
from _common import *  # noqa: F401,F403 - sys.path bootstrap + fact/emit/die + os/json/sys

import argparse
import datetime

from tradingagents.eval.acceptance.oracles.market_client import FinnhubMarketOracle


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--asof", default=None)
    args = parser.parse_args(argv)

    if args.asof is not None:
        try:
            asof_date = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            print(f"invalid --asof {args.asof!r} (expected YYYY-MM-DD)", file=sys.stderr)  # noqa: F405
            return 2
    else:
        asof_date = datetime.date.today()

    if asof_date != datetime.date.today():
        print("finnhub_oracle.py is current-day only (Finnhub /quote has no "
              "as-of param); refusing a past/future --asof to avoid a "
              "silent look-ahead mislabel", file=sys.stderr)
        return 3

    try:
        quote = FinnhubMarketOracle().quote(args.ticker, args.asof)
    except RuntimeError as e:  # FINNHUB_API_KEY not set
        print(e, file=sys.stderr)  # noqa: F405
        return 2
    except Exception as e:  # urllib HTTPError/URLError, json decode, anything else
        print(e, file=sys.stderr)  # noqa: F405
        return 1

    close = quote.get("close")
    if close is None:
        print(f"no close price in Finnhub /quote response for {args.ticker}",
              file=sys.stderr)  # noqa: F405
        return 3

    asof = asof_date.isoformat()
    emit({"P1.px_finnhub_oob": fact(float(close), "USD", asof, "finnhub")})  # noqa: F405
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))  # noqa: F405
