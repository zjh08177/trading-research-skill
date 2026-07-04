"""Tiingo out-of-band price oracle for the P1 cross-check.

Emits `P1.px_close_oob` (settled close, the primary cross-check). With `--live`
on a current-day run it also emits `P1.px_last_oob` — the /iex intraday
reference price — to cross-check the live schwab quote against an independent
source. The live check is best-effort: its absence never fails the CLI.
"""
from _common import *  # noqa: F401,F403 - sys.path bootstrap + fact/emit/die + os/json/sys

import argparse
import datetime
import urllib.parse
import urllib.request

from tradingagents.eval.acceptance.oracles.market_client import FinnhubMarketOracle

TIINGO_IEX = "https://api.tiingo.com/iex"


def build_facts(rows, asof):
    """Pick the last settled bar with date <= asof; empty dict when none usable."""
    rows = [r for r in rows if r.get("date") and r["date"] <= asof and r.get("close") is not None]
    if not rows:
        return {}
    last = max(rows, key=lambda r: r["date"])
    return {"P1.px_close_oob": fact(last["close"], "USD", last["date"], "tiingo")}  # noqa: F405


def fetch_iex_last(ticker):
    """(price, iso_ts) from Tiingo /iex tngoLast; raises on missing key/empty/HTTP error."""
    key = os.environ.get("TIINGO_API_KEY")  # noqa: F405
    if not key:
        raise RuntimeError("TIINGO_API_KEY not set")
    query = urllib.parse.urlencode({"token": key})
    url = f"{TIINGO_IEX}/{ticker.lower()}?{query}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - https only
        rows = json.load(resp)  # noqa: F405
    row = (rows or [{}])[0]
    px = row.get("tngoLast") if row.get("tngoLast") is not None else row.get("last")
    if px is None:
        raise ValueError("no tngoLast/last in /iex response")
    return float(px), row.get("timestamp")


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--asof", default=None)
    parser.add_argument(
        "--live", action="store_true",
        help="also emit P1.px_last_oob (/iex intraday cross-check) on a current-day run",
    )
    args = parser.parse_args(argv)
    if args.asof is not None:
        try:
            asof_date = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            print(f"invalid --asof {args.asof!r} (expected YYYY-MM-DD)", file=sys.stderr)  # noqa: F405
            return 2
    else:
        asof_date = datetime.date.today()
    asof = asof_date.isoformat()  # normalized: settled row-compare and the --live guard agree
    try:
        # Dummy finnhub key: only TIINGO_API_KEY is required for bars().
        rows = FinnhubMarketOracle(api_key="unused").bars(args.ticker)
    except RuntimeError as e:  # TIINGO_API_KEY not set
        print(e, file=sys.stderr)  # noqa: F405
        return 2
    except Exception as e:  # urllib HTTPError/URLError, json decode, anything else
        print(e, file=sys.stderr)  # noqa: F405
        return 1
    facts = build_facts(rows, asof)
    if not facts:
        print(f"no settled Tiingo bar for {args.ticker} on or before {asof}", file=sys.stderr)  # noqa: F405
        return 3
    # Live cross-check: only for a current-day run — a past as_of must never
    # receive today's /iex price (look-ahead), and a future one has no live datum.
    # Best-effort: on failure the settled emission stands and the CLI still exits 0.
    if args.live and asof_date == datetime.date.today():
        try:
            px, ts = fetch_iex_last(args.ticker)
            asof_ts = ts or datetime.datetime.now(datetime.timezone.utc).isoformat()
            facts["P1.px_last_oob"] = fact(px, "USD", asof_ts, "tiingo")  # noqa: F405
        except Exception as e:  # noqa: BLE001 - cross-check is optional
            print(f"iex cross-check unavailable: {e}", file=sys.stderr)  # noqa: F405
    emit(facts)  # noqa: F405
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))  # noqa: F405
