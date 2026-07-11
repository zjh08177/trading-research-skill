"""P6 Reddit/WSB tone CLI: Tradestie top-50 WSB sentiment leaderboard (keyless).

Fetches https://tradestie.com/api/v1/apps/reddit?date=YYYY-MM-DD and emits the
requested ticker's row (or an explicit not-ranked marker) under one private key
for the `reddit_tone` distiller. Absence from the top-50 is a valid signal
(no WSB crowding), never an error.

Replay contract (ERD R9/§5): `--replay` fetches STRICTLY prior-day data
(`date < cutoff`) — daily aggregates must not import same-day/EOD sentiment at
an intraday cutoff. The resolved leaderboard date is always emitted so the
distiller can assert `asof <= cutoff`.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from _common import die, emit

# Current API host per https://tradestie.com/apps/reddit/api/ (the legacy
# tradestie.com/api/v1 path is a permanent 404). Dates are MM-DD-YYYY there.
# VERIFIED DOWN 2026-07-11 (socket closed from two independent networks) —
# this CLI fail-louds and the registry degrades the feed to a named gap.
API_URL = "https://api.tradestie.com/v1/apps/reddit"
MAX_LOOKBACK_DAYS = 5
TIMEOUT = 20
UA = "trading-research-skill/1.0 (personal decision-support; contact: local)"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_leaderboard(day_iso):
    """One dated leaderboard call. Returns a list (possibly empty)."""
    d = date.fromisoformat(day_iso)
    rows = _get(f"{API_URL}?date={d.strftime('%m-%d-%Y')}")
    return rows if isinstance(rows, list) else []


def resolve_leaderboard(start_day, fetch=fetch_leaderboard):
    """Walk back from `start_day` to the newest day with data (weekends/gaps).

    Returns (resolved_date_iso, rows). Raises LookupError when no day within
    MAX_LOOKBACK_DAYS carries data — fail-loud, never a silent empty pack.
    """
    day = date.fromisoformat(str(start_day)[:10])
    for _ in range(MAX_LOOKBACK_DAYS + 1):
        rows = fetch(day.isoformat())
        if rows:
            return day.isoformat(), rows
        day -= timedelta(days=1)
    raise LookupError(
        f"tradestie: no leaderboard data within {MAX_LOOKBACK_DAYS} days back from {start_day}")


def build_payload(ticker, asof, replay=False, fetch=fetch_leaderboard):
    canonical = ticker.upper()
    start = date.fromisoformat(str(asof)[:10])
    if replay:
        start -= timedelta(days=1)  # strictly prior-day: date < cutoff
    resolved, rows = resolve_leaderboard(start.isoformat(), fetch=fetch)
    if replay and resolved >= str(asof)[:10]:
        raise AssertionError(
            f"tradestie replay guard: resolved date {resolved} not strictly before cutoff {asof}")
    row = next((r for r in rows if str(r.get("ticker", "")).upper() == canonical), None)
    return {"_tradestie": {
        "date": resolved,
        "ticker": canonical,
        "row": row,           # null => not on the top-50 WSB leaderboard that day
        "n_ranked": len(rows),
    }}


def main(argv):
    p = argparse.ArgumentParser(prog="tradestie")
    p.add_argument("--ticker", required=True)
    p.add_argument("--asof", default=datetime.now().date().isoformat())
    p.add_argument("--replay", action="store_true", default=False)
    args = p.parse_args(argv)
    try:
        payload = build_payload(args.ticker, args.asof, replay=args.replay)
    except urllib.error.HTTPError as e:
        die(f"tradestie HTTP {e.code}: {e.reason}", 4 if e.code == 429 else 1)
    except (LookupError, AssertionError) as e:
        die(str(e), 3)
    except Exception as e:
        die(f"{type(e).__name__}: {e}", 1)
    emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
