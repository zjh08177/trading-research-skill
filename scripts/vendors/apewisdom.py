"""P6 Reddit crowding CLI: ApeWisdom mention/rank leaderboard (keyless).

Fetches https://apewisdom.io/api/v1.0/filter/all-stocks/page/N and emits the
requested ticker's row (rank, mentions, 24h deltas) under one private key for
the `reddit_crowding` distiller. Absence from the scanned pages is a valid
"not-ranked" outcome (no measurable Reddit crowding), never an error.

Replay contract (ERD R9/§5): ApeWisdom exposes ONLY rolling 24h snapshots — no
point-in-time history — so this feed is hard `replay_safe=False` (the registry
omits-and-names it in replay; this CLI never receives --replay).
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime

from _common import die, emit

API_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
MAX_PAGES = 3  # 100/page -> top ~300 names; beyond that crowding is noise
TIMEOUT = 20
UA = "trading-research-skill/1.0 (personal decision-support; contact: local)"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_page(page):
    payload = _get(API_URL.format(page=page))
    return payload.get("results") or []


def build_payload(ticker, asof, fetch=fetch_page):
    canonical = ticker.upper()
    scanned = 0
    row = None
    for page in range(1, MAX_PAGES + 1):
        results = fetch(page)
        if not results:
            break
        scanned += len(results)
        row = next((r for r in results
                    if str(r.get("ticker", "")).upper() == canonical), None)
        if row is not None:
            break
    if scanned == 0:
        raise LookupError("apewisdom: leaderboard returned no results (endpoint change?)")
    return {"_apewisdom": {
        "asof": str(asof)[:10],
        "ticker": canonical,
        "row": row,            # null => not in the top `scanned` crowding ranks
        "scanned_ranks": scanned,
    }}


def main(argv):
    p = argparse.ArgumentParser(prog="apewisdom")
    p.add_argument("--ticker", required=True)
    p.add_argument("--asof", default=datetime.now().date().isoformat())
    args = p.parse_args(argv)
    try:
        payload = build_payload(args.ticker, args.asof)
    except urllib.error.HTTPError as e:
        die(f"apewisdom HTTP {e.code}: {e.reason}", 4 if e.code == 429 else 1)
    except LookupError as e:
        die(str(e), 3)
    except Exception as e:
        die(f"{type(e).__name__}: {e}", 1)
    emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
