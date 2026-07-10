"""Schwab fundamentals CLI: read-only raw passthrough of the Schwab quotes
endpoint's `fundamental` block (fields=quote,fundamental).

Raw, not distilled: this CLI emits the untouched vendor `fundamental` dict
under one private key so a LATER distiller step can decide real-world units
per field (the R4/AC-E IV-trap) rather than guessing here. It is additive and
read-only — it calls the module-level `schwab._request` seam directly and
never touches `SchwabQuoteVendor`, so the existing P1.last quote path
(`schwab_quote.py`) is untouched.
"""
import argparse
import sys

from _common import die, emit

from tradingagents.dataflows import schwab
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)


def fetch_fundamental(ticker):
    """Raw `fundamental` dict for `ticker`, or {} when Schwab reports none.

    Raises NoMarketDataError only when the symbol itself is absent from the
    response (no entry to read at all) — a present entry with no/empty
    `fundamental` key is a valid "vendor has nothing here" outcome, not an
    error, so it must never crash the caller.
    """
    sym = ticker.upper()
    payload = schwab._request(schwab.SCHWAB_QUOTES_PATH, {"symbols": sym, "fields": "quote,fundamental"})
    entry = payload.get(sym)
    if entry is None:
        raise NoMarketDataError(ticker, None, "Schwab returned no entry for symbol")
    return entry.get("fundamental") or {}


def main(argv):
    p = argparse.ArgumentParser(prog="schwab_fundamental")
    p.add_argument("--ticker", required=True)
    args = p.parse_args(argv)
    try:
        fundamental = fetch_fundamental(args.ticker)
    except VendorNotConfiguredError as e:  # incl. SchwabReauthRequiredError; before ValueError
        die(str(e), 2)
    except NoMarketDataError as e:
        die(str(e), 3)
    except VendorRateLimitError as e:
        die(str(e), 4)
    except Exception as e:
        die("%s: %s" % (type(e).__name__, e), 1)
    emit({"_schwab_fundamental": fundamental})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
