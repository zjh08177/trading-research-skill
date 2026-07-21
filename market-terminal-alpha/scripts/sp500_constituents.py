#!/usr/bin/env python3
"""S&P 500 constituent list from Unusual Whales (first-choice vendor).

MarketTerminal's screener has NO index-membership filter, so we source the
S&P 500 universe ourselves from UW's SPY holdings and intersect it with MT's
screener/prediction output.

UW endpoint: GET /api/etfs/SPY/holdings?limit=600  -> ~504 rows (503 stocks + cash line)
Each row: ticker, weight, sector, name, close, market data, ...

Usage:
    python3 sp500_constituents.py            # prints tickers, one per line
    python3 sp500_constituents.py --json     # full rows as JSON
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts", "vendors"))
import _uw_common as uw  # noqa: E402


def sp500_rows(etf="SPY", limit=600):
    """Return the ETF's holdings rows from UW (dropping non-equity/cash lines)."""
    status, body = uw.get_json(f"/api/etfs/{etf}/holdings", {"limit": limit})
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"UW {etf} holdings HTTP {status}")
    rows = body.get("data") or []
    # keep real equity holdings (a ticker present); UW tags a cash/other line without one
    return [r for r in rows if (r.get("ticker") or "").strip()]


def sp500_tickers(etf="SPY", limit=600):
    return sorted({r["ticker"].strip().upper() for r in sp500_rows(etf, limit)})


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--etf", default="SPY", help="index ETF proxy (SPY=S&P500, QQQ=Nasdaq100, IWM=R2000)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = sp500_rows(args.etf)
    if args.json:
        print(json.dumps({"etf": args.etf, "n": len(rows), "rows": rows}, indent=1))
    else:
        for t in sorted({r["ticker"].strip().upper() for r in rows}):
            print(t)
    print(f"# {len(rows)} constituents from UW {args.etf} holdings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
