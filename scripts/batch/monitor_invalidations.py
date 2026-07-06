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
    """Load every published levels file. SKIP any file missing the ticker envelope
    (a raw 56-levels.json hand-dropped here — without ticker/kind/asof — would
    otherwise KeyError in the scan loop and kill the WHOLE monitor). Returns
    (registry, malformed_filenames)."""
    reg, malformed = [], []
    for f in sorted(glob.glob(os.path.join(levels_dir, "*.json"))):
        try:
            e = json.load(open(f))
        except Exception:
            malformed.append(os.path.basename(f))
            continue
        if not isinstance(e, dict) or not e.get("ticker"):
            malformed.append(os.path.basename(f))
            continue
        reg.append(e)
    return reg, malformed


def held_from_holdings(holdings):
    """Set of held symbols from a snaptrade_holdings.py dump
    ({'holdings':[{'symbol',...}]})."""
    return {h.get("symbol") for h in (holdings or {}).get("holdings", []) if h.get("symbol")}


def load_holdings_dump(path):
    """Load a holdings dump from a file that is EITHER a raw snaptrade_holdings.py
    dump ({'holdings':[...]}) OR a snapshot_holdings.py envelope
    ({'vendor':{'holdings':[...]}}). The daily snapshot is the single holdings
    SSOT, so both the monitor and action_plan read the same file — unwrap the
    envelope's verbatim vendor payload when present."""
    obj = json.load(open(path))
    return obj.get("vendor", obj)


def fetch_held():
    """Current portfolio symbols via snaptrade_holdings.py (live, read-only). Returns
    a set, or None when holdings can't be determined (auth/exit!=0) so the caller
    falls back to the full registry instead of silently monitoring nothing. RAISES
    on an import/config failure (wrong interpreter): a silent full-registry fallback
    would mask holdings-scoping being off — the exact regression this monitor fixes."""
    try:
        r = subprocess.run([PY, os.path.join(VENDORS, "snaptrade_holdings.py")],
                           capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        if "ModuleNotFoundError" in r.stderr or "ImportError" in r.stderr:
            raise RuntimeError(
                f"snaptrade_holdings.py could not import its deps under {PY}. Run the "
                "monitor with the venv carrying the SnapTrade SDK + python-dotenv "
                f"(see requirements.txt), not a bare interpreter.\n{r.stderr.strip()}")
        return None
    if not r.stdout.strip():
        return None
    try:
        return held_from_holdings(json.loads(r.stdout))
    except json.JSONDecodeError:
        return None


def filter_to_held(reg, held, keep_crypto=True):
    """Scope the registry to names the user CURRENTLY HOLDS — ad-hoc analyses (a
    levels file for an unheld name like UNH) are dropped. Crypto is kept regardless:
    registry crypto entries only come from the held-crypto batch, and crypto's
    holdings source differs from SnapTrade. held=None -> no filter (scan all).
    Returns (kept, dropped_tickers)."""
    if held is None:
        return reg, []
    kept, dropped = [], []
    for e in reg:
        if (keep_crypto and e.get("kind") == "crypto") or e.get("ticker") in held:
            kept.append(e)
        else:
            dropped.append(e.get("ticker"))
    return kept, dropped


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
    scan_all = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    holdings_file = None
    if "--holdings" in argv:                       # SSOT wiring: read the day's snapshot
        i = argv.index("--holdings")               # instead of a fresh live fetch
        holdings_file = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    if not argv:
        sys.stderr.write("usage: monitor_invalidations.py <levels_dir> [out_md] [asof] "
                         "[--all] [--holdings <snapshot.json>]\n")
        return 2
    levels_dir = argv[0]
    out_md = argv[1] if len(argv) > 1 else None
    asof = argv[2] if len(argv) > 2 else ""
    reg, malformed = load_registry(levels_dir)

    # Scope to CURRENT holdings (the point of a monitor) unless --all. Prefer the
    # daily snapshot file (--holdings) as the single holdings SSOT; else fetch live.
    # If holdings can't be determined, fall back to the full registry + a loud note
    # — never blind the monitor on a SnapTrade outage.
    if scan_all:
        held = None
    elif holdings_file:
        held = held_from_holdings(load_holdings_dump(holdings_file))
    else:
        held = fetch_held()
    reg, not_held = filter_to_held(reg, held)
    scope = ("full registry (--all)" if scan_all else
             ("HOLDINGS UNAVAILABLE — scanned full registry" if held is None else
              f"scoped to {len(held)} current holdings"))
    cp = crypto_prices()

    fired, unavailable = [], []
    for e in reg:
        price = cp.get(e["ticker"]) if e.get("kind") == "crypto" else equity_price(e["ticker"])
        for r in evaluate(e, price):
            if r["action"] == "PRICE UNAVAILABLE":
                unavailable.append(r["ticker"])
            elif r["fired"]:
                fired.append(r)

    skips = []
    if not_held:
        skips.append(f"{len(not_held)} not-held skipped ({', '.join(t for t in not_held if t)})")
    if malformed:
        skips.append(f"{len(malformed)} malformed skipped ({', '.join(malformed)})")
    if unavailable:
        skips.append(f"{len(unavailable)} price-unavailable ({', '.join(unavailable)})")
    lines = [f"# Invalidation monitor — {asof or 'live'}", "",
             f"Scanned **{len(reg)}** holdings ({scope}) · **{len(fired)}** triggers fired"
             + ("" if not skips else " · " + " · ".join(skips)) + ".", ""]
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
        # Fired-trigger sidecar (additive; md bytes unchanged): the portfolio_delta
        # join reads these monitor-<date>.json files, not the rendered md. Empty
        # fired day writes [] so a gap day is a witnessed no-trigger, not a hole.
        sidecar = os.path.splitext(out_md)[0] + ".json"
        with open(sidecar, "w") as f:
            json.dump(sorted(fired, key=lambda x: x["ticker"]), f)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
