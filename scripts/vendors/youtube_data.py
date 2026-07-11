"""P6 YouTube attention CLI: official Data API v3 `search.list` (BYO key).

Counts recent finance videos about the ticker (`publishedAfter` window) and
emits the count plus a few video IDs (cite-by-ID, ERD R14) under one private
key for the `youtube_attention` distiller.

Compliance (ERD R12/R14): BYO `YOUTUBE_API_KEY` via vendors.env — never
hardcoded, OFF-by-default for distributed installs (the registry gates the
feed on key presence). Only distilled scalars persist; titles/descriptions are
transient and never emitted. One `search.list` call = 100 quota units of the
10k/day free budget (~90-100 tickers/day ceiling, ERD §7).

Replay contract (ERD R9/§5): hard `replay_safe=False` — comment/publish-time
look-ahead makes YouTube non-PIT; the registry omits-and-names it in replay.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from _common import die, emit

API_URL = "https://www.googleapis.com/youtube/v3/search"
TIMEOUT = 20
MAX_RESULTS = 50   # one page only: bounded quota (100 units/call regardless)
CITE_IDS = 5


def fetch_search(ticker, published_after_iso, api_key):
    params = urllib.parse.urlencode({
        "part": "id",
        "type": "video",
        "q": f'"{ticker}" stock',
        "order": "date",
        "publishedAfter": published_after_iso,
        "maxResults": MAX_RESULTS,
        "relevanceLanguage": "en",
        "key": api_key,
    })
    req = urllib.request.Request(f"{API_URL}?{params}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_payload(ticker, asof, days, payload):
    ids = [item["id"]["videoId"] for item in payload.get("items", [])
           if isinstance(item.get("id"), dict) and item["id"].get("videoId")]
    return {"_youtube": {
        "asof": str(asof)[:10],
        "ticker": ticker.upper(),
        "video_count": len(ids),
        "window_days": days,
        "capped_at": MAX_RESULTS,
        "video_ids": ids[:CITE_IDS],   # cite-by-ID only; no titles/UGC persisted
    }}


def main(argv):
    p = argparse.ArgumentParser(prog="youtube_data")
    p.add_argument("--ticker", required=True)
    p.add_argument("--asof", default=datetime.now().date().isoformat())
    p.add_argument("--days", type=int, default=7)
    args = p.parse_args(argv)
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        die("youtube_data: YOUTUBE_API_KEY not set in vendors.env (BYO key, ERD R12)", 2)
    start = datetime.fromisoformat(str(args.asof)[:10]) - timedelta(days=args.days)
    published_after = start.strftime("%Y-%m-%dT00:00:00Z")
    try:
        raw = fetch_search(args.ticker.upper(), published_after, api_key)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        code = 4 if e.code in (403, 429) else 1  # 403 = quotaExceeded/keyInvalid family
        die(f"youtube_data HTTP {e.code}: {e.reason} {body}", code)
    except Exception as e:
        die(f"{type(e).__name__}: {e}", 1)
    emit(build_payload(args.ticker, args.asof, args.days, raw))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
