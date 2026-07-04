"""SEC EDGAR fundamentals CLI: P3.* facts from ONE companyfacts fetch.

Usage: edgar_fundamentals.py --ticker AAPL [--asof YYYY-MM-DD]
Success: single-line compact JSON on stdout (omitted keys, never null).
Errors: one line to stderr, nothing on stdout.
Exit: 0 ok / 2 config-auth / 3 no-data / 4 rate-limit / 1 other.
"""
import argparse
import datetime as dt
import json
import sys

from _common import fact, emit, die  # sys.path + .env bootstrap; must import first

from tradingagents.dataflows import edgar
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

SRC = "sec-edgar"
REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")
DEBT_EXTRA_TAGS = ("DebtCurrent", "ShortTermBorrowings", "CommercialPaper")


def _rows(facts, taxonomy, tag):
    concept = facts.get("facts", {}).get(taxonomy, {}).get(tag, {})
    out = []
    for rows in concept.get("units", {}).values():
        out.extend(rows)
    return out


def _span_days(row):
    return (dt.date.fromisoformat(row["end"]) - dt.date.fromisoformat(row["start"])).days


def _dedupe(rows):
    """Keep the max-'filed' row per (start, end): restatements duplicate periods."""
    best = {}
    for r in rows:
        k = (r.get("start"), r["end"])
        if k not in best or r.get("filed", "") > best[k].get("filed", ""):
            best[k] = r
    return sorted(best.values(), key=lambda r: r["end"])


def _durations(facts, tag, asof, form, lo, hi):
    rows = [
        r
        for r in _rows(facts, "us-gaap", tag)
        if r.get("start") and r.get("form") == form and r.get("end", "") <= asof
        and lo <= _span_days(r) <= hi
    ]
    return _dedupe(rows)


def _quarters(facts, tag, asof):
    """Discrete quarters: 80-100 day span AND form 10-Q (10-Qs also carry YTD rows)."""
    return _durations(facts, tag, asof, "10-Q", 80, 100)


def _fiscal_years(facts, tag, asof):
    return _durations(facts, tag, asof, "10-K", 350, 370)


def _ttm(facts, tag, asof):
    """(sum of 4 most recent discrete quarters ending <= asof, last end) or None."""
    q = _quarters(facts, tag, asof)
    if len(q) < 4:
        return None
    return sum(r["val"] for r in q[-4:]), q[-1]["end"]


def _prev_ttm(facts, tag, asof):
    q = _quarters(facts, tag, asof)
    if len(q) < 8:
        return None
    return sum(r["val"] for r in q[-8:-4])


def _instant(facts, taxonomy, tag, asof):
    """(val, end) of the latest instant row (no 'start') ending <= asof, or None."""
    rows = [r for r in _rows(facts, taxonomy, tag) if "start" not in r and r.get("end", "") <= asof]
    if not rows:
        return None
    r = max(rows, key=lambda r: (r["end"], r.get("filed", "")))
    return r["val"], r["end"]


def _total_debt(facts, asof):
    parts = []
    ltd_nc = _instant(facts, "us-gaap", "LongTermDebtNoncurrent", asof)
    if ltd_nc is not None:
        parts.append(ltd_nc)
        ltd_c = _instant(facts, "us-gaap", "LongTermDebtCurrent", asof)
        if ltd_c is not None:
            parts.append(ltd_c)
    else:
        ltd = _instant(facts, "us-gaap", "LongTermDebt", asof)
        if ltd is not None:
            parts.append(ltd)
    for tag in DEBT_EXTRA_TAGS:
        p = _instant(facts, "us-gaap", tag, asof)
        if p is not None:
            parts.append(p)
    if not parts:
        return None  # no debt tag found: emit nothing, never fabricate 0
    return sum(v for v, _ in parts), max(e for _, e in parts)


def _latest_filed(facts, form):
    best = None
    for taxonomy in facts.get("facts", {}).values():
        for concept in taxonomy.values():
            for rows in concept.get("units", {}).values():
                for r in rows:
                    if r.get("form") == form and r.get("filed"):
                        if best is None or r["filed"] > best["filed"]:
                            best = r
    return best


def build_facts(facts, asof=None):
    asof = asof or "9999-12-31"
    out = {}
    rev = prev_rev = None
    for tag in REVENUE_TAGS:
        rev = _ttm(facts, tag, asof)
        if rev is not None:
            prev_rev = _prev_ttm(facts, tag, asof)
            break
    if rev is not None:
        rev_v, rev_end = rev
        out["P3.revenue_ttm"] = fact(rev_v, "USD", rev_end, SRC)
        if prev_rev:
            out["P3.revenue_yoy"] = fact((rev_v / prev_rev - 1) * 100, "pct", rev_end, SRC)
    eps = _ttm(facts, "EarningsPerShareDiluted", asof)
    if eps is not None:
        out["P3.eps_diluted_ttm"] = fact(eps[0], "USD", eps[1], SRC)
        prev_eps = _prev_ttm(facts, "EarningsPerShareDiluted", asof)
        if prev_eps:
            out["P3.eps_yoy"] = fact((eps[0] / prev_eps - 1) * 100, "pct", eps[1], SRC)
    if rev is not None and rev_v:
        gp = _ttm(facts, "GrossProfit", asof)
        cor = _ttm(facts, "CostOfRevenue", asof)
        if gp is not None:
            out["P3.gross_margin_ttm"] = fact(gp[0] / rev_v * 100, "pct", rev_end, SRC)
        elif cor is not None:
            out["P3.gross_margin_ttm"] = fact((rev_v - cor[0]) / rev_v * 100, "pct", rev_end, SRC)
        for tag, key in (
            ("OperatingIncomeLoss", "P3.operating_margin_ttm"),
            ("NetIncomeLoss", "P3.net_margin_ttm"),
        ):
            t = _ttm(facts, tag, asof)
            if t is not None:
                out[key] = fact(t[0] / rev_v * 100, "pct", rev_end, SRC)
    # FY-based FCF: 10-Q cash-flow rows are fiscal-YTD cumulative, not quarters.
    ocf = _fiscal_years(facts, "NetCashProvidedByUsedInOperatingActivities", asof)
    capex = _fiscal_years(facts, "PaymentsToAcquirePropertyPlantAndEquipment", asof)
    if ocf and capex:
        o = ocf[-1]
        c = next((r for r in reversed(capex) if r["end"] == o["end"]), None)
        if c is not None:
            out["P3.fcf_ttm"] = fact(o["val"] - c["val"], "USD", o["end"], SRC)
    debt = _total_debt(facts, asof)
    if debt is not None:
        out["P3.total_debt"] = fact(debt[0], "USD", debt[1], SRC)
    cash = _instant(facts, "us-gaap", "CashAndCashEquivalentsAtCarryingValue", asof)
    if cash is not None:
        out["P3.cash_and_equivalents"] = fact(cash[0], "USD", cash[1], SRC)
    if debt is not None and cash is not None:
        out["P3.net_debt"] = fact(debt[0] - cash[0], "USD", max(debt[1], cash[1]), SRC)
    shares = _instant(facts, "dei", "EntityCommonStockSharesOutstanding", asof)
    if shares is not None:
        out["P3.shares_outstanding"] = fact(shares[0], "shares", shares[1], SRC)
    for form, key in (("10-K", "P3.latest_10k_filed"), ("10-Q", "P3.latest_10q_filed")):
        row = _latest_filed(facts, form)
        if row is not None:
            out[key] = fact(row["filed"], "date", row.get("end", row["filed"]), SRC)
    return out


def main(argv):
    parser = argparse.ArgumentParser(prog="edgar_fundamentals")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--asof", default=None)
    args = parser.parse_args(argv)
    try:
        edgar.get_user_agent()  # cheap config preflight before any network call
        payload = json.loads(edgar.get_fundamentals(args.ticker, args.asof))
        facts = build_facts(payload, args.asof)
    except VendorNotConfiguredError as e:
        die(str(e), 2)
    except NoMarketDataError as e:
        die(str(e), 3)
    except VendorRateLimitError as e:
        die(str(e), 4)
    except Exception as e:  # requests/JSON/Key/Value errors and anything else
        die(f"edgar_fundamentals: {e}", 1)
    if not facts:
        die(f"no derivable fundamentals for {args.ticker!r}", 3)
    emit(facts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
