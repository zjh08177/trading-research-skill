"""Full-history Tiingo CLI: unbounded daily OHLCV for the P9 left-side-signal
scripts (base-rate/cluster/percentile studies need 15+ years of history, not
the 500-day window baked into the vendored FinnhubMarketOracle.bars()
helper). Same TIINGO_API_KEY credential as tiingo_oracle.py, same endpoint,
just an explicit early startDate. Stdlib only.

Usage: tiingo_history.py --ticker X --asof Y [--start YYYY-MM-DD]
Exit 0 ok; 2 bad args / missing TIINGO_API_KEY; 3 no bars on/before asof; 4 rate-limited after retries; 1 other."""
from _common import *  # noqa: F401,F403 - sys.path bootstrap + fact/emit/die + os/json/sys

import argparse
import datetime
import time
import urllib.error
import urllib.parse
import urllib.request

TIINGO_DAILY = "https://api.tiingo.com/tiingo/daily"
DEFAULT_START = "2000-01-01"
RETRIES = 3                      # mirrors _uw_common.RETRIES_429
RETRY_STATUS = {429, 500, 502, 503, 504}


def fetch_history(ticker, start):
    """Raw Tiingo daily rows (unfiltered) from `start` to latest. Transient
    failures (429/5xx/network) retry with backoff, mirroring _uw_common's 429
    pattern; non-retryable HTTP errors raise immediately. Never fabricates."""
    key = os.environ.get("TIINGO_API_KEY")  # noqa: F405
    if not key:
        raise RuntimeError("TIINGO_API_KEY not set")
    query = urllib.parse.urlencode({"token": key, "startDate": start})
    url = f"{TIINGO_DAILY}/{ticker.lower()}/prices?{query}"
    last_err = None
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - https only
                return json.load(resp) or []  # noqa: F405
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS:
                raise
            last_err = e
        except urllib.error.URLError as e:      # DNS, refused, socket timeout
            last_err = e
        if attempt < RETRIES:
            time.sleep(2.0 * (attempt + 1))     # 2s, 4s, 6s
    raise last_err


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
    except urllib.error.HTTPError as e:
        print(f"tiingo HTTP {e.code} after retries: {e}", file=sys.stderr)  # noqa: F405
        return 4 if e.code == 429 else 1
    except Exception as e:  # noqa: BLE001 - URLError, json decode, ...
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
