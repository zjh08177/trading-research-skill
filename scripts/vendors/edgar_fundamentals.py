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


def filed_on_or_before(row, cutoff):
    """True only if row has a truthy 'filed' date and it is <= cutoff.

    Both 'filed' and cutoff are ISO YYYY-MM-DD strings, so a plain string
    compare is safe. Without this gate a 10-Q whose period ended before the
    cutoff but was FILED after it would leak into a replay pack.

    In live mode the cutoff is the sentinel "9999-12-31"; the filing-date gate
    is then a no-op (rows lacking a 'filed' field are kept, preserving the
    pre-replay live behavior byte-for-byte). The gate only bites under a real
    replay cutoff.
    """
    if cutoff == "9999-12-31":
        return True
    filed = row.get("filed")
    return bool(filed) and filed <= cutoff


def _durations(facts, tag, asof, form, lo, hi):
    rows = [
        r
        for r in _rows(facts, "us-gaap", tag)
        if r.get("start") and r.get("form") == form and r.get("end", "") <= asof
        and lo <= _span_days(r) <= hi
        and filed_on_or_before(r, asof)
    ]
    return _dedupe(rows)


def _quarters(facts, tag, asof):
    """Discrete quarters: 80-100 day span AND form 10-Q (10-Qs also carry YTD rows)."""
    return _durations(facts, tag, asof, "10-Q", 80, 100)


def _fiscal_years(facts, tag, asof):
    return _durations(facts, tag, asof, "10-K", 350, 370)


def _ttm(facts, tag, asof):
    """(sum, last end, known_at) of the 4 most recent discrete quarters ending
    <= asof, or None. known_at is the max 'filed' among the contributing
    quarters: the TTM sum isn't knowable until all four are filed."""
    q = _quarters(facts, tag, asof)
    if len(q) < 4:
        return None
    used = q[-4:]
    return sum(r["val"] for r in used), used[-1]["end"], max(r["filed"] for r in used)


def _prev_ttm(facts, tag, asof):
    """(sum, known_at) of the prior 4-quarter window, or None."""
    q = _quarters(facts, tag, asof)
    if len(q) < 8:
        return None
    used = q[-8:-4]
    return sum(r["val"] for r in used), max(r["filed"] for r in used)


def _instant(facts, taxonomy, tag, asof):
    """(val, end, filed) of the latest instant row (no 'start') ending <= asof
    and filed <= asof, or None."""
    rows = [
        r
        for r in _rows(facts, taxonomy, tag)
        if "start" not in r and r.get("end", "") <= asof and filed_on_or_before(r, asof)
    ]
    if not rows:
        return None
    r = max(rows, key=lambda r: (r["end"], r.get("filed", "")))
    return r["val"], r["end"], r["filed"]


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
    return (
        sum(v for v, _, _ in parts),
        max(e for _, e, _ in parts),
        max(f for _, _, f in parts),
    )


def _latest_filed(facts, form, asof):
    """Latest row for `form` with filed <= asof, or None."""
    best = None
    for taxonomy in facts.get("facts", {}).values():
        for concept in taxonomy.values():
            for rows in concept.get("units", {}).values():
                for r in rows:
                    if r.get("form") == form and filed_on_or_before(r, asof):
                        if best is None or r["filed"] > best["filed"]:
                            best = r
    return best


def build_facts(facts, asof=None):
    replay = asof is not None  # explicit --asof cutoff -> point-in-time replay
    asof = asof or "9999-12-31"
    out = {}

    def emit_fact(key, v, unit, per_asof, known_at=None):
        f = fact(v, unit, per_asof, SRC)
        if replay and known_at:
            f["known_at"] = known_at
        out[key] = f

    rev = prev_rev = None
    for tag in REVENUE_TAGS:
        rev = _ttm(facts, tag, asof)
        if rev is not None:
            prev_rev = _prev_ttm(facts, tag, asof)
            break
    if rev is not None:
        rev_v, rev_end, rev_known_at = rev
        emit_fact("P3.revenue_ttm", rev_v, "USD", rev_end, rev_known_at)
        if prev_rev is not None and prev_rev[0]:
            prev_rev_v, prev_rev_known_at = prev_rev
            emit_fact(
                "P3.revenue_yoy", (rev_v / prev_rev_v - 1) * 100, "pct", rev_end,
                max(rev_known_at, prev_rev_known_at),
            )
    eps = _ttm(facts, "EarningsPerShareDiluted", asof)
    if eps is not None:
        eps_v, eps_end, eps_known_at = eps
        emit_fact("P3.eps_diluted_ttm", eps_v, "USD", eps_end, eps_known_at)
        prev_eps = _prev_ttm(facts, "EarningsPerShareDiluted", asof)
        if prev_eps is not None and prev_eps[0]:
            prev_eps_v, prev_eps_known_at = prev_eps
            emit_fact(
                "P3.eps_yoy", (eps_v / prev_eps_v - 1) * 100, "pct", eps_end,
                max(eps_known_at, prev_eps_known_at),
            )
    if rev is not None and rev_v:
        gp = _ttm(facts, "GrossProfit", asof)
        cor = _ttm(facts, "CostOfRevenue", asof)
        if gp is not None:
            gp_v, _gp_end, gp_known_at = gp
            emit_fact(
                "P3.gross_margin_ttm", gp_v / rev_v * 100, "pct", rev_end,
                max(rev_known_at, gp_known_at),
            )
        elif cor is not None:
            cor_v, _cor_end, cor_known_at = cor
            emit_fact(
                "P3.gross_margin_ttm", (rev_v - cor_v) / rev_v * 100, "pct", rev_end,
                max(rev_known_at, cor_known_at),
            )
        for tag, key in (
            ("OperatingIncomeLoss", "P3.operating_margin_ttm"),
            ("NetIncomeLoss", "P3.net_margin_ttm"),
        ):
            t = _ttm(facts, tag, asof)
            if t is not None:
                t_v, _t_end, t_known_at = t
                emit_fact(key, t_v / rev_v * 100, "pct", rev_end, max(rev_known_at, t_known_at))
    # FY-based FCF: 10-Q cash-flow rows are fiscal-YTD cumulative, not quarters.
    ocf = _fiscal_years(facts, "NetCashProvidedByUsedInOperatingActivities", asof)
    capex = _fiscal_years(facts, "PaymentsToAcquirePropertyPlantAndEquipment", asof)
    if ocf and capex:
        o = ocf[-1]
        c = next((r for r in reversed(capex) if r["end"] == o["end"]), None)
        if c is not None:
            emit_fact(
                "P3.fcf_ttm", o["val"] - c["val"], "USD", o["end"],
                max(o["filed"], c["filed"]),
            )
    debt = _total_debt(facts, asof)
    if debt is not None:
        debt_v, debt_end, debt_known_at = debt
        emit_fact("P3.total_debt", debt_v, "USD", debt_end, debt_known_at)
    cash = _instant(facts, "us-gaap", "CashAndCashEquivalentsAtCarryingValue", asof)
    if cash is not None:
        cash_v, cash_end, cash_known_at = cash
        emit_fact("P3.cash_and_equivalents", cash_v, "USD", cash_end, cash_known_at)
    if debt is not None and cash is not None:
        emit_fact(
            "P3.net_debt", debt_v - cash_v, "USD", max(debt_end, cash_end),
            max(debt_known_at, cash_known_at),
        )
    shares = _instant(facts, "dei", "EntityCommonStockSharesOutstanding", asof)
    if shares is not None:
        shares_v, shares_end, shares_known_at = shares
        emit_fact("P3.shares_outstanding", shares_v, "shares", shares_end, shares_known_at)
    for form, key in (("10-K", "P3.latest_10k_filed"), ("10-Q", "P3.latest_10q_filed")):
        row = _latest_filed(facts, form, asof)
        if row is not None:
            emit_fact(key, row["filed"], "date", row.get("end", row["filed"]), row["filed"])
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
