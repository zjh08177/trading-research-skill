#!/usr/bin/env python3
"""Test MarketTerminal's screener JSON API: fetch a full custom-filtered universe
with AI predictions, paginated.

This is a standalone API test (the seed of the pipeline's capture layer). It proves:
  - POST /v1/screener/find applies arbitrary numeric filters (e.g. marketCap >= 10B)
  - it returns aiPrediction.daily + ratings for the whole filtered universe
  - limit is capped at 100/page, so it must be paginated

Auth (interim): export a Bearer access token from a logged-in session:
    export MT_ACCESS_TOKEN='eyJ...'         # the sb-<ref>-auth-token access_token JWT
Full headless auth (Supabase password grant) is Layer 1 of the pipeline — see docs/PROPOSAL.md.

Usage:
    MT_ACCESS_TOKEN=... python3 test_screener.py --min-mcap 10e9 [--out universe.json]
"""
import argparse, json, os, re, sys, time, urllib.request

API = "https://api.marketterminal.com/v1/screener/find"
COLUMNS = ["ticker", "quote.marketCap", "quote.close",
           "aiPrediction.daily", "ratings.overallRating"]


def _body(min_mcap, page, limit=100):
    return {
        "filters": {
            "quote.marketCap": {
                "isNumberQuery": True, "path": "quote.marketCap",
                "values": {"min": int(min_mcap), "type": "above",
                           "title": f"Above ${int(min_mcap/1e9)}B"},
            }
        },
        "page": page, "limit": limit,
        "sortKey": "quote.marketCap", "sortOrder": "desc",
        "columns": COLUMNS,
    }


def _post(token, payload):
    # MarketTerminal's /screener/find is PUBLIC — auth optional (sent only if provided).
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_universe(token, min_mcap, sleep_ms=250):
    first = _post(token, _body(min_mcap, 1))
    total_pages = first.get("totalPages", 1)
    docs = list(first.get("docs", []))
    for p in range(2, total_pages + 1):
        time.sleep(sleep_ms / 1000)
        docs.extend(_post(token, _body(min_mcap, p)).get("docs", []))
    return first.get("totalDocs"), total_pages, docs


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mcap", type=float, default=10e9,
                    help="minimum market cap (default 10e9 = $10B)")
    ap.add_argument("--out", default=None, help="write full universe JSON here")
    args = ap.parse_args(argv)

    token = os.environ.get("MT_ACCESS_TOKEN")  # optional — endpoint is public
    total, pages, docs = fetch_universe(token, args.min_mcap)
    is_fund = re.compile(r"^[A-Z]{4}X$")   # 5-char ticker ending in X ~ mutual fund
    funds = [d for d in docs if is_fund.match(d.get("ticker") or "")]
    with_pred = [d for d in docs if d.get("aiPredictionDaily") is not None]
    stocks = [d for d in docs if not is_fund.match(d.get("ticker") or "")]

    print(json.dumps({
        "filter": f"marketCap >= ${args.min_mcap/1e9:.0f}B",
        "totalDocs": total, "totalPages": pages, "fetched": len(docs),
        "with_prediction": len(with_pred),
        "fund_like": len(funds), "common_stock_est": len(stocks),
        "first5": [d.get("ticker") for d in docs[:5]],
    }, indent=2))

    if args.out:
        json.dump({"filter_min_mcap": args.min_mcap, "n": len(docs), "rows": docs},
                  open(args.out, "w"), indent=1)
        print(f"wrote {len(docs)} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
