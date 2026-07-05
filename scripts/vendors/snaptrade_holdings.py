"""SnapTrade full-portfolio holdings CLI (read-only).

Emits every LONG equity/fund/crypto holding aggregated by symbol across ALL
SnapTrade-linked accounts (Robinhood, Schwab, Fidelity, ...). Options excluded.
Same read-only, cross-broker guarantees as ``snaptrade_account.py``; this is the
portfolio-wide view (the per-ticker view is ``snaptrade_account.py``).

Output (stdout JSON):
  {"as_of": ISO8601, "total_book": float, "n_accounts": int,
   "holdings": [{"symbol","kind","qty","price","market_value","avg_cost",
                 "unrealized_pl","unrealized_pl_pct","pct_of_book","brokers",
                 "n_accounts"}...]}   sorted by market_value desc.

``avg_cost``/``unrealized_pl*`` are null when cost_basis is absent for any lot of
that symbol. Live-only: a past/future ``--asof`` yields exit 3.
"""
import argparse
import datetime
import json
import sys

from _snaptrade_common import die, die_from_exc
from snaptrade_account import _f, _kind, _sym, fetch, total_book


def build_holdings(accounts, positions_by_acct):
    """Aggregate long holdings by symbol across accounts. Pure (no I/O)."""
    book = total_book(accounts, positions_by_acct)
    inst_by_id = {a["id"]: (a.get("institution") or "?") for a in accounts}
    agg = {}  # symbol -> accumulator
    for aid, plist in positions_by_acct.items():
        inst = inst_by_id.get(aid, "?")
        for p in plist or []:
            sym = _sym(p)
            if not sym or _kind(p) == "option":
                continue
            units = _f(p.get("units"))
            price = _f(p.get("price"))
            if units is None or units <= 0 or price is None:
                continue
            a = agg.setdefault(sym, {"symbol": sym, "kind": _kind(p), "qty": 0.0,
                                     "market_value": 0.0, "cost": 0.0,
                                     "have_cost": True, "brokers": [],
                                     "accts": set()})
            a["qty"] += units
            a["market_value"] += units * price
            cb = _f(p.get("cost_basis"))
            if cb is None:
                a["have_cost"] = False
            else:
                a["cost"] += units * cb
            a["accts"].add(aid)
            if inst not in a["brokers"]:
                a["brokers"].append(inst)
    out = []
    for a in agg.values():
        mv, qty, cost = a["market_value"], a["qty"], a["cost"]
        row = {
            "symbol": a["symbol"], "kind": a["kind"], "qty": qty,
            # volume-weighted so qty*price == market_value (self-consistent row)
            "price": (mv / qty) if qty else 0.0, "market_value": mv,
            "pct_of_book": (100.0 * mv / book) if book else 0.0,
            "brokers": ", ".join(a["brokers"]), "n_accounts": len(a["accts"]),
            "avg_cost": (cost / qty) if (a["have_cost"] and qty) else None,
            "unrealized_pl": (mv - cost) if a["have_cost"] else None,
            "unrealized_pl_pct": (100.0 * (mv - cost) / cost)
            if (a["have_cost"] and cost) else None,
        }
        out.append(row)
    out.sort(key=lambda r: r["market_value"], reverse=True)
    return {"total_book": book, "holdings": out}


def main(argv):
    p = argparse.ArgumentParser(prog="snaptrade_holdings")
    p.add_argument("--asof", default=None)
    args = p.parse_args(argv)
    if args.asof is not None:
        try:
            asof = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            die("invalid --asof %r (expected YYYY-MM-DD)" % args.asof, 2)
        if asof != datetime.date.today():
            die("holdings are live-only (got %s)" % args.asof, 3)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        accounts, positions_by_acct, failed = fetch("")  # ticker unused by fetch
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        die_from_exc(e)
    res = build_holdings(accounts, positions_by_acct)
    print(json.dumps({"as_of": stamp, "n_accounts": len(accounts),
                      "accounts_skipped": len(failed), **res},
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
