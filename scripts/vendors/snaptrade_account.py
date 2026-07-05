"""SnapTrade cross-broker position CLI: emits H1 facts for ``--ticker`` (read-only).

Aggregates the owner's LONG holding in ``--ticker`` across ALL SnapTrade-linked
brokerage accounts (Robinhood, Schwab, Fidelity, ...). Same ``H1.*`` fact
contract as ``schwab_account.py`` but cross-broker — this is the fix for the
Schwab-only position gap (a holding at any linked broker is now API-verified,
not screenshot-built). The emitted artifact is withheld from analysts/debate/
risk/judges (invariant 12) and read only by the writer and qa_check. Live-only:
a past/future ``--asof`` yields no position (exit 3). Read-only: it lists
accounts and positions only — no order/trade endpoint (invariant 13).

Position schema (SnapTrade v11 ``get_all_account_positions``): each item has
``instrument.{raw_symbol,symbol,kind}``, and string ``units``/``price``/
``cost_basis`` (per-share avg cost; absent for some accounts). Long-only:
``units > 0`` and ``kind != "option"``. Market value = units*price; unrealized
P/L is derived from cost_basis and is emitted only when cost_basis is present
for every matched lot.
"""
import argparse
import datetime
import sys

from _snaptrade_common import client, die, die_from_exc, emit, fact, plain, user_creds

SRC = "snaptrade"


def _f(x):
    """Parse a SnapTrade numeric (often a string) to float; None on absence."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _sym(p):
    ins = p.get("instrument") or p.get("symbol") or {}
    if not isinstance(ins, dict):
        return ""
    inner = ins.get("symbol")
    if isinstance(inner, dict):  # tolerate nested UniversalSymbol shape
        return (inner.get("raw_symbol") or inner.get("symbol") or "").upper()
    return (ins.get("raw_symbol") or ins.get("symbol") or "").upper()


def _kind(p):
    ins = p.get("instrument") or {}
    return (ins.get("kind") or ins.get("type") or "").lower() if isinstance(ins, dict) else ""


def _acct_long_mv(positions):
    """Sum of an account's long (non-option) position market values."""
    s = 0.0
    for p in positions or []:
        u, pr = _f(p.get("units")), _f(p.get("price"))
        if u is not None and u > 0 and pr is not None and _kind(p) != "option":
            s += u * pr
    return s


def total_book(accounts, positions_by_acct):
    """Book denominator kept consistent with the position numerator: use each
    account's reported total_value, or (when it is missing/None) fall back to
    that account's own long-position market value. This guarantees a held
    account is never dropped from the denominator while its positions stay in
    the numerator — which would otherwise inflate pct_of_book past 100%."""
    tot = 0.0
    for a in accounts:
        tv = _f(a.get("total_value"))
        tot += tv if tv is not None else _acct_long_mv(positions_by_acct.get(a["id"]) or [])
    return tot


def build_position(ticker, accounts, positions_by_acct):
    """Aggregate the long holding in ``ticker`` across accounts. Pure (no I/O).

    ``accounts``: list of {"id", "institution", "total_value"}.
    ``positions_by_acct``: {account_id: [raw position dict, ...]}.
    Returns an aggregate dict, or None when the ticker is not held long.
    """
    t = ticker.upper()
    book = total_book(accounts, positions_by_acct)
    inst_by_id = {a["id"]: (a.get("institution") or "?") for a in accounts}

    qty = 0.0
    market_value = 0.0
    cost = 0.0
    have_cost = True          # false if any matched lot lacks cost_basis
    have_price = True         # false if any matched lot lacks a live price
    brokers = []              # distinct institutions, first-seen order
    n_accounts = 0

    for aid, plist in positions_by_acct.items():
        acct_matched = False
        for p in plist or []:
            if _sym(p) != t or _kind(p) == "option":
                continue
            units = _f(p.get("units"))
            if units is None or units <= 0:
                continue          # long-only; skip shorts / zero
            acct_matched = True
            qty += units          # count the shares even if unpriced (never drop qty)
            price = _f(p.get("price"))
            if price is None:
                have_price = False
            else:
                market_value += units * price
            cb = _f(p.get("cost_basis"))
            if cb is None:
                have_cost = False
            else:
                cost += units * cb
        if acct_matched:
            n_accounts += 1
            inst = inst_by_id.get(aid, "?")
            if inst not in brokers:
                brokers.append(inst)

    if qty <= 0:
        return None

    agg = {
        "qty": qty,
        "n_accounts": n_accounts,
        "brokers": ", ".join(brokers),
        "have_price": have_price,
        "have_cost": have_cost,
    }
    # market_value / pct / P/L are omitted when a matched lot lacks a live price
    # (their value would be understated). Held + qty are always reported.
    if have_price:
        agg["market_value"] = market_value
        agg["pct_of_book"] = (100.0 * market_value / book) if book else 0.0
        if have_cost:
            agg["avg_price"] = (cost / qty) if qty else 0.0
            agg["unrealized_pl"] = market_value - cost
            agg["unrealized_pl_pct"] = (100.0 * (market_value - cost) / cost) if cost else 0.0
    return agg


def build_facts(agg, asof):
    """H1 facts. Flat position -> a single positive H1.held=false. Value-derived
    facts (market_value/pct/avg/P&L) appear only when they were computable."""
    if agg is None:
        return {"H1.held": fact(False, "bool", asof, SRC)}
    f = {
        "H1.held": fact(True, "bool", asof, SRC),
        "H1.qty": fact(agg["qty"], "shares", asof, SRC),
        "H1.n_accounts": fact(agg["n_accounts"], "count", asof, SRC),
        "H1.brokers": fact(agg["brokers"], "label", asof, SRC),
    }
    if "market_value" in agg:
        f["H1.market_value"] = fact(agg["market_value"], "USD", asof, SRC)
        f["H1.pct_of_book"] = fact(agg["pct_of_book"], "%", asof, SRC)
    if "avg_price" in agg:
        f["H1.avg_price"] = fact(agg["avg_price"], "USD", asof, SRC)
        f["H1.unrealized_pl"] = fact(agg["unrealized_pl"], "USD", asof, SRC)
        f["H1.unrealized_pl_pct"] = fact(agg["unrealized_pl_pct"], "%", asof, SRC)
    return f


def fetch(ticker):
    """List accounts + positions from SnapTrade.

    Returns ``(accounts, positions_by_acct, failed_account_ids)``. An empty
    account set is data-absence, NOT a flat position — it exits 2 so the
    orchestrator routes the Schwab fallback (a held-elsewhere ticker would
    otherwise read as a confident, wrong ``held=false``). A single account whose
    positions call fails transiently is skipped (recorded in ``failed``) instead
    of aborting the whole cross-broker read; a genuine auth failure hard-fails.
    """
    uid, us = user_creds()
    c = client()
    raw_accts = plain(c.account_information.list_user_accounts(
        user_id=uid, user_secret=us).body) or []
    accounts = []
    for a in raw_accts:
        bal = (a.get("balance") or {}).get("total") or {}
        accounts.append({"id": a.get("id"),
                         "institution": a.get("institution_name") or a.get("brokerage"),
                         "total_value": bal.get("amount")})
    if not accounts:
        die("no linked SnapTrade brokerage accounts to inspect (unconfigured or "
            "all connections dropped)", 2)
    positions_by_acct = {}
    failed = []
    for a in accounts:
        try:
            body = plain(c.account_information.get_all_account_positions(
                user_id=uid, user_secret=us, account_id=a["id"]).body)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status", None) in (401, 403):
                # genuine auth failure: hard-fail (exit 2), independent of type
                die("SnapTrade auth/authorization failed on account read (%s)"
                    % getattr(e, "status", None), 2)
            failed.append(a["id"])        # transient: skip this account, keep going
            positions_by_acct[a["id"]] = []
            continue
        positions_by_acct[a["id"]] = body if isinstance(body, list) else (
            (body or {}).get("results") or (body or {}).get("positions") or [])
    return accounts, positions_by_acct, failed


def main(argv):
    p = argparse.ArgumentParser(prog="snaptrade_account")
    p.add_argument("--ticker", required=True)
    # Positions are a live snapshot; a past as_of would need historical holdings
    # (unavailable), a future one has none. Compare parsed dates to the local
    # calendar date, matching schwab_account.py / schwab_quote.py.
    p.add_argument("--asof", default=None)
    args = p.parse_args(argv)
    if args.asof is not None:
        try:
            asof = datetime.datetime.strptime(args.asof, "%Y-%m-%d").date()
        except ValueError:
            die("invalid --asof %r (expected YYYY-MM-DD)" % args.asof, 2)
        if asof != datetime.date.today():
            die("account positions are live-only (got %s); back-dated runs carry "
                "no position" % args.asof, 3)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        accounts, positions_by_acct, failed = fetch(args.ticker)
    except SystemExit:
        raise                                   # die()/user_creds already mapped
    except Exception as e:                       # noqa: BLE001 — mapped to codes
        die_from_exc(e)
    agg = build_position(args.ticker, accounts, positions_by_acct)
    facts = build_facts(agg, stamp)
    if failed:
        # surface partial coverage so a held=false is not read as certain when an
        # account could not be inspected this run
        facts["H1.accounts_skipped"] = fact(len(failed), "count", stamp, SRC)
    emit(facts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
