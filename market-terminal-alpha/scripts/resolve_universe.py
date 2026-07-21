#!/usr/bin/env python3
"""Pipeline universe definition: (index constituents from UW) ∩ (MarketTerminal screener).

MarketTerminal has no index filter and uses a different class-share ticker format than UW
(UW `BRKB` / MT `BRK-B` / others `BRK.B`). We join on a SEPARATOR-STRIPPED key so class shares
match automatically — no hardcoded symbol map — and carry BOTH ticker forms so downstream code
calls each vendor in its own dialect (MT predict with `mt_ticker`, UW bars with `uw_ticker`).

Output row per constituent:
    {key, uw_ticker, mt_ticker, sp500_weight, sector, mt_market_cap, mt_pred_daily}
`mt_ticker`/`mt_pred_daily` are null when the constituent isn't in the MT screener result
(too small for the market-cap floor, or delisted/renamed) — reported as a coverage gap.

Auth: UW via ~/.config/tradingagents (already configured); MT via MT_ACCESS_TOKEN (interim —
Layer-1 Supabase headless auth replaces this). See docs `design-proposal`.

Usage:
    MT_ACCESS_TOKEN=... python3 resolve_universe.py --index-etf SPY --mt-min-mcap 1e9 --out universe.json
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sp500_constituents as idx          # UW index holdings
import test_screener as mt                # MT screener fetch (fetch_universe)


def norm(t):
    """Separator-insensitive join key: strip non-alphanumerics, uppercase. BRKB==BRK-B==BRK.B."""
    return re.sub(r"[^A-Z0-9]", "", (t or "").upper())


def build(index_etf, mt_min_mcap, token):
    # 1) index constituents from UW (first-choice vendor)
    idx_rows = idx.sp500_rows(index_etf)
    idx_by = {}
    for r in idx_rows:
        k = norm(r.get("ticker"))
        if k:
            idx_by[k] = r

    # 2) MarketTerminal screener universe (>= floor), carries aiPredictionDaily
    _, _, mt_docs = mt.fetch_universe(token, mt_min_mcap)
    mt_by = {norm(d.get("ticker")): d for d in mt_docs if norm(d.get("ticker"))}

    # 3) join on normalized key
    out = []
    for k, ir in idx_by.items():
        md = mt_by.get(k)
        out.append({
            "key": k,
            "uw_ticker": ir.get("ticker"),
            "mt_ticker": md.get("ticker") if md else None,
            "sp500_weight": ir.get("weight"),
            "sector": ir.get("sector"),
            "mt_market_cap": md.get("marketCap") if md else None,
            "mt_pred_daily": md.get("aiPredictionDaily") if md else None,
        })
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-etf", default="SPY", help="SPY=S&P500, QQQ=Nasdaq100, IWM=R2000")
    ap.add_argument("--mt-min-mcap", type=float, default=1e9,
                    help="MT screener market-cap floor (low = full index coverage, more pages)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    token = os.environ.get("MT_ACCESS_TOKEN")  # optional — MT endpoints are public
    rows = build(args.index_etf, args.mt_min_mcap, token)
    matched = [r for r in rows if r["mt_ticker"]]
    with_pred = [r for r in matched if r["mt_pred_daily"] is not None]
    gap = [r["uw_ticker"] for r in rows if not r["mt_ticker"]]

    print(json.dumps({
        "index_etf": args.index_etf, "mt_min_mcap": args.mt_min_mcap,
        "constituents": len(rows),
        "matched_in_MT": len(matched), "with_prediction": len(with_pred),
        "unmatched": len(gap), "unmatched_examples": sorted(gap)[:12],
    }, indent=2))

    if args.out:
        json.dump({"index_etf": args.index_etf, "n": len(rows), "rows": rows},
                  open(args.out, "w"), indent=1)
        print(f"wrote {len(rows)} constituents -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
