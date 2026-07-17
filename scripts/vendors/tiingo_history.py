"""Full-history Tiingo CLI: unbounded daily OHLCV for the P9 left-side-signal
scripts (base-rate/cluster/percentile studies need 15+ years of history, not
the 500-day window baked into the vendored FinnhubMarketOracle.bars()
helper). Same TIINGO_API_KEY credential as tiingo_oracle.py, same endpoint,
just an explicit early startDate. Stdlib only.

Usage: tiingo_history.py --ticker X --asof Y [--start YYYY-MM-DD]
Exit 0 ok; 2 bad args / missing TIINGO_API_KEY; 3 no bars on/before asof."""
from _common import *  # noqa: F401,F403 - sys.path bootstrap + fact/emit/die + os/json/sys

import argparse
import datetime
import urllib.error
import urllib.parse
import urllib.request

TIINGO_DAILY = "https://api.tiingo.com/tiingo/daily"
DEFAULT_START = "2000-01-01"


def fetch_history(ticker, start):
    """Raw Tiingo daily rows (unfiltered) from `start` to latest. Raises
    RuntimeError (no key) or urllib errors (network/HTTP) — never fabricates."""
    key = os.environ.get("TIINGO_API_KEY")  # noqa: F405
    if not key:
        raise RuntimeError("TIINGO_API_KEY not set")
    query = urllib.parse.urlencode({"token": key, "startDate": start})
    url = f"{TIINGO_DAILY}/{ticker.lower()}/prices?{query}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - https only
        return json.load(resp) or []  # noqa: F405


def build_bars(rows, asof):
    """Ascending bars with date <= asof, ISO date sliced to YYYY-MM-DD."""
    out = []
    for r in rows:
        d = str(r.get("date", ""))[:10]
        if not d or d > asof:
            continue
        if r.get("close") is None:
            continue
        out.append({
            "date": d,
            "close": r["close"],
            "adjClose": r.get("adjClose", r["close"]),
            "volume": r.get("volume", 0),
        })
    out.sort(key=lambda b: b["date"])
    return out


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--asof", required=True)
    parser.add_argument("--start", default=DEFAULT_START)
    args = parser.parse_args(argv)
    try:
        datetime.datetime.strptime(args.asof, "%Y-%m-%d")
    except ValueError:
        print(f"invalid --asof {args.asof!r} (expected YYYY-MM-DD)", file=sys.stderr)  # noqa: F405
        return 2
    try:
        rows = fetch_history(args.ticker, args.start)
    except RuntimeError as e:
        print(e, file=sys.stderr)  # noqa: F405
        return 2
    except Exception as e:  # noqa: BLE001 - urllib HTTPError/URLError, json decode, ...
        print(e, file=sys.stderr)  # noqa: F405
        return 1
    bars = build_bars(rows, args.asof)
    if not bars:
        print(f"no Tiingo bar on or before {args.asof} for {args.ticker}", file=sys.stderr)  # noqa: F405
        return 3
    emit_history({"ticker": args.ticker.upper(), "asof": args.asof, "bars": bars})  # noqa: F405
    return 0


def emit_history(payload):
    print(json.dumps(payload, separators=(",", ":")))  # noqa: F405


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))  # noqa: F405
