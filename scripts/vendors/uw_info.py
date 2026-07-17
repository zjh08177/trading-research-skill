"""Unusual Whales company-info CLI: raw passthrough of `/api/stock/{ticker}/info`.

Emits the untouched UW `info` dict under one private key so a distiller decides
which fields to surface — mirroring `schwab_fundamental.py`'s raw-passthrough
contract. The active use is P3.beta (the one schwab.fundamental field UW covers
1:1); short-interest-to-float, PEG, and dividends are NOT available at this tier
and stay gapped.

Exit codes: 0 ok, 2 auth (401 / missing key), 3 no data (404 / empty),
4 rate-limit (429), 1 other.
"""
import argparse

import _uw_common as uw


def fetch_info(ticker):
    status, body = uw.get_json("/api/stock/%s/info" % ticker)
    if status == 401:
        uw.die("UW auth failed (401): check key in %s" % uw.CREDS_PATH, 2)
    if status == 429:
        uw.die("UW rate limited (429) on info/%s" % ticker, 4)
    if status == 404:
        uw.die("UW no data (404) for %s" % ticker, 3)
    if status != 200:
        uw.die("UW HTTP %s on info/%s: %s" % (status, ticker, str(body)[:160]), 1)
    info = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(info, dict) or not info:
        uw.die("UW malformed/empty info for %s" % ticker, 3)
    return info


def main(argv):
    p = argparse.ArgumentParser(prog="uw_info")
    p.add_argument("--ticker", required=True)
    args = p.parse_args(argv)
    uw.emit({"_uw_info": fetch_info(args.ticker.upper())})
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
