"""SEC EDGAR fundamentals vendor.

Fetches company fundamentals from the SEC's free, keyless XBRL ``companyfacts``
API (https://data.sec.gov). A ticker is first resolved to a 10-digit zero-padded
CIK via the public ticker map, then the company's reported facts are pulled and
look-ahead filtered to the caller's ``curr_date`` so a past simulation date can
never see a fiscal period that ended in its future.

SEC fair-access policy *requires* a descriptive ``User-Agent`` ("Name email") or
it returns HTTP 403; this is read from ``SEC_EDGAR_USER_AGENT``. If that variable
is unset the vendor raises ``EdgarNotConfiguredError`` so the routing layer treats
it as "unavailable" rather than a hard crash. Throttling (403/429) raises
``EdgarRateLimitError`` so the router skips to the next configured vendor.

Mirrors the fred.py idiom: a single mockable ``_request(path, params)`` seam means
the unit tests run fully offline with ``mock.patch.object(edgar, "_request", ...)``.
"""
import json
import logging
import os

import requests

from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

logger = logging.getLogger(__name__)

DATA_BASE_URL = "https://data.sec.gov"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

# Network timeout (seconds) so a stalled SEC call can't hang the agents,
# mirroring the FRED / Alpha Vantage clients.
REQUEST_TIMEOUT = 30

# Curated us-gaap concept tags projected out of companyfacts for each statement.
# companyfacts returns every reported concept; the per-statement views keep the
# response focused so the analyst isn't flooded with the full XBRL dump.
BALANCE_SHEET_TAGS = (
    "Assets",
    "AssetsCurrent",
    "Liabilities",
    "LiabilitiesCurrent",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "RetainedEarningsAccumulatedDeficit",
    "CommonStockValue",
)
CASHFLOW_TAGS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
INCOME_STATEMENT_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
)


class EdgarNotConfiguredError(VendorNotConfiguredError):
    """SEC_EDGAR_USER_AGENT unset (SEC rejects anonymous User-Agents).

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """


class EdgarRateLimitError(VendorRateLimitError):
    """SEC fair-access throttling (HTTP 403/429); the router tries the next vendor."""


def get_user_agent() -> str:
    """Retrieve the SEC EDGAR User-Agent from the environment."""
    ua = os.getenv("SEC_EDGAR_USER_AGENT")
    if not ua:
        raise EdgarNotConfiguredError(
            "SEC_EDGAR_USER_AGENT environment variable is not set. SEC requires a "
            "descriptive 'Name email@example.com' User-Agent for its EDGAR APIs."
        )
    return ua


def _request(path: str, params: dict | None = None) -> dict:
    """GET a full SEC URL with the required User-Agent; classify failure by behavior.

    ``path`` is the FULL url because EDGAR spans two hosts (data.sec.gov for the
    XBRL APIs, www.sec.gov for the ticker map), so the test stub dispatches on a
    url substring (mirroring fred's dispatch-on-path).
    """
    response = requests.get(
        path,
        params=params or {},
        headers={
            "User-Agent": get_user_agent(),
            "Accept-Encoding": "gzip, deflate",
        },
        timeout=REQUEST_TIMEOUT,
    )
    # SEC throttles fair-access abusers with 403/429; treat as a transient
    # rate-limit so the router skips to the next configured vendor.
    if response.status_code in (403, 429):
        raise EdgarRateLimitError(
            f"SEC EDGAR throttled the request: HTTP {response.status_code}."
        )
    response.raise_for_status()  # other 4xx/5xx bubble -> router logs + falls back/raises
    return response.json()


def _resolve_cik(ticker: str) -> str:
    """Resolve a ticker to its 10-digit zero-padded CIK via the SEC ticker map.

    Raises ``NoMarketDataError`` when the ticker is absent from the map (the SEC
    does not cover it), so the router emits the NO_DATA sentinel rather than a
    vendor-specific empty string.
    """
    mapping = _request(TICKER_MAP_URL)  # {"0": {"cik_str": 320193, "ticker": "AAPL", ...}}
    up = ticker.upper()
    for row in mapping.values():
        if str(row.get("ticker", "")).upper() == up:
            return f"{int(row['cik_str']):010d}"
    raise NoMarketDataError(ticker, detail="ticker not found in SEC EDGAR ticker map")


def _company_facts(ticker: str) -> dict:
    """Fetch the company's full XBRL companyfacts payload (resolving CIK first)."""
    cik = _resolve_cik(ticker)
    url = f"{DATA_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        return _request(url)
    except json.JSONDecodeError as e:  # GB6-a: a malformed/empty/HTML 200 body
        # SEC sometimes returns a non-JSON 200 (throttle interstitial / truncation).
        # Degrade like the sibling vendors instead of leaking a raw parse error
        # (AMD "Expecting value: line 291") into the report.
        raise NoMarketDataError(
            ticker, detail="unparseable SEC EDGAR companyfacts JSON"
        ) from e
    except requests.HTTPError as e:  # 404 = CIK has no XBRL coverage
        if e.response is not None and e.response.status_code == 404:
            raise NoMarketDataError(
                ticker, detail="no SEC EDGAR coverage (unknown CIK)"
            ) from e
        raise


def _filter_facts_by_date(facts: dict, curr_date: str | None) -> dict:
    """Drop XBRL rows whose period end is after ``curr_date`` (real look-ahead guard).

    Unlike the Alpha Vantage filter (which receives a string and is a no-op),
    companyfacts is pure JSON, so we parse the nested
    ``facts[taxonomy][concept]["units"][unit]`` row lists and filter each in
    place. ISO-8601 ``end`` dates compare correctly as strings.
    """
    if not curr_date or not isinstance(facts, dict):
        return facts
    for taxonomy in facts.get("facts", {}).values():
        for concept in taxonomy.values():
            units = concept.get("units", {})
            for unit, rows in units.items():
                units[unit] = [r for r in rows if r.get("end", "") <= curr_date]
    return facts


def _project_statement(facts: dict, tags) -> dict:
    """Keep only the requested us-gaap concept tags, plus the company identifiers."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    projected = {tag: us_gaap[tag] for tag in tags if tag in us_gaap}
    return {
        "cik": facts.get("cik"),
        "entityName": facts.get("entityName"),
        "facts": {"us-gaap": projected},
    }


# Public surface — signatures MUST match alpha_vantage_fundamentals exactly: the
# router calls these positionally, so any reorder would mis-bind args at runtime.
def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """Retrieve comprehensive fundamentals for a ticker as look-ahead-filtered JSON.

    Args:
        ticker: Ticker symbol of the company.
        curr_date: Current trading date (yyyy-mm-dd); fiscal periods ending after
            it are dropped so a past date never leaks future data.

    Returns:
        ``json.dumps`` of the full SEC companyfacts payload, date-filtered.
    """
    return json.dumps(_filter_facts_by_date(_company_facts(ticker), curr_date))


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Retrieve balance-sheet concepts for a ticker as look-ahead-filtered JSON."""
    facts = _filter_facts_by_date(_company_facts(ticker), curr_date)
    return json.dumps(_project_statement(facts, BALANCE_SHEET_TAGS))


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Retrieve cash-flow concepts for a ticker as look-ahead-filtered JSON."""
    facts = _filter_facts_by_date(_company_facts(ticker), curr_date)
    return json.dumps(_project_statement(facts, CASHFLOW_TAGS))


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Retrieve income-statement concepts for a ticker as look-ahead-filtered JSON."""
    facts = _filter_facts_by_date(_company_facts(ticker), curr_date)
    return json.dumps(_project_statement(facts, INCOME_STATEMENT_TAGS))
