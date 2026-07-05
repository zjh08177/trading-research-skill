#!/usr/bin/env python3
"""Daily invalidation monitor (v2.3 workstream F).

Reads every live decision-levels registry (published by publish_levels.py), pulls the
current price per holding (equities/ETFs via schwab_quote; crypto via Crypto.com public
REST), and reports which downside/upside triggers have FIRED — each with the action the
report prescribed (Sell / Exit / Trim / Add / re-rate…). Zero fired → one all-clear line.
Writes a vault monitor-<date>.md and prints a summary; wire to a weekday pre-market
routine. Price fetch is injectable so evaluate() is unit-tested without network.

Usage: monitor_invalidations.py <levels_dir> [out_md] [asof]
  levels_dir = <vault_reports>/levels  (publish_levels.py output)
"""
import glob
import json
import os
import subprocess
import sys
import urllib.request

SK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENDORS = SK + "/scripts/vendors"
PY = sys.executable
CRYPTO_REST = "https://api.crypto.com/exchange/v1/public/get-tickers"


def load_registry(levels_dir):
    reg = []
    for f in sorted(glob.glob(os.path.join(levels_dir, "*.json"))):
        reg.append(json.load(open(f)))
    return reg


def crypto_prices():
    """One batched public call → {TICKER: last_price}. Empty dict on failure."""
    try:
        d = json.load(urllib.request.urlopen(CRYPTO_REST, timeout=15))
        out = {}
        for r in d["result"]["data"]:
            inst = r.get("i", "")
            if inst.endswith("_USDT") and r.get("a") is not None:
                out[inst[:-5]] = float(r["a"])
        return out
    except Exception:
        return {}


def equity_price(ticker):
    """schwab_quote P1.last (realtime NBBO intraday; last settled close otherwise)."""
    try:
        r = subprocess.run([PY, os.path.join(VENDORS, "schwab_quote.py"), "--ticker", ticker],
                           capture_output=True, text=True, timeout=40)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return float(json.loads(r.stdout).get("P1.last", {}).get("v"))
    except Exception:
        return None


def evaluate(entry, price):
    """Pure: return the list of FIRED triggers for one holding at `price`.
    downside fires when price <= level; upside fires when price >= level."""
    if price is None:
        return [{"ticker": entry["ticker"], "dir": "?", "fired": False, "price": None,
                 "level": None, "action": "PRICE UNAVAILABLE", "basis": ""}]
    fired = []
    dn, up = entry.get("downside"), entry.get("upside")
    if dn and dn.get("level") is not None and price <= dn["level"]:
        fired.append({"ticker": entry["ticker"], "dir": "▼", "fired": True, "price": price,
                      "level": dn["level"], "action": dn.get("action", ""), "basis": dn.get("basis", "")})
    if up and up.get("level") is not None and price >= up["level"]:
        fired.append({"ticker": entry["ticker"], "dir": "▲", "fired": True, "price": price,
                      "level": up["level"], "action": up.get("action", ""), "basis": up.get("basis", "")})
    return fired


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.stderr.write("usage: monitor_invalidations.py <levels_dir> [out_md] [asof]\n")
        return 2
    levels_dir = argv[0]
    out_md = argv[1] if len(argv) > 1 else None
    asof = argv[2] if len(argv) > 2 else ""
    reg = load_registry(levels_dir)
    cp = crypto_prices()

    fired, unavailable = [], []
    for e in reg:
        price = cp.get(e["ticker"]) if e.get("kind") == "crypto" else equity_price(e["ticker"])
        for r in evaluate(e, price):
            if r["action"] == "PRICE UNAVAILABLE":
                unavailable.append(r["ticker"])
            elif r["fired"]:
                fired.append(r)

    lines = [f"# Invalidation monitor — {asof or 'live'}", "",
             f"Scanned **{len(reg)}** holdings · **{len(fired)}** triggers fired"
             + (f" · {len(unavailable)} price-unavailable ({', '.join(unavailable)})" if unavailable else "") + ".", ""]
    if fired:
        lines += ["| Holding | Dir | Price | Trigger | Basis | Action |",
                  "|---|---|---|---|---|---|"]
        for r in sorted(fired, key=lambda x: x["ticker"]):
            lines.append(f"| {r['ticker']} | {r['dir']} | {r['price']:g} | {r['level']:g} | "
                         f"{r['basis']} | **{r['action']}** |")
    else:
        lines.append("**All clear** — no holding has crossed a decision level.")
    md = "\n".join(lines) + "\n"

    if out_md:
        os.makedirs(os.path.dirname(out_md), exist_ok=True)
        open(out_md, "w").write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
