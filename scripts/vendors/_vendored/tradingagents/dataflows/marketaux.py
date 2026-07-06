"""Marketaux news vendor with per-entity sentiment.

Fetches market news from the Marketaux ``/v1/news/all`` endpoint and renders a
formatted Markdown report for the LLM news analyst (like ``yfinance_news``), NOT
raw JSON. The historical window is enforced server-side via ``published_after`` /
``published_before`` so a past simulation date can never see future news
(look-ahead safety). Per-article sentiment is reported only for the queried
symbol: Marketaux returns a sentiment score per detected entity, and we filter
``entities[]`` to the requested ticker so a different symbol's score is never
mis-attributed.

Requires ``MARKETAUX_API_KEY``; if it is unset the vendor raises
``MarketauxNotConfiguredError`` so the routing layer treats it as "unavailable"
rather than a hard crash. Usage/rate limits (HTTP 402/429 or a JSON error code)
raise ``MarketauxRateLimitError`` so the router skips to the next configured
vendor.

Mirrors the edgar.py / fred.py idiom: a single mockable ``_request(path, params)``
seam means the unit tests run fully offline with
``mock.patch.object(marketaux, "_request", ...)``.
"""
import logging
import os
from datetime import datetime

import requests

from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from .symbol_utils import normalize_symbol

logger = logging.getLogger(__name__)

API_URL = "https://api.marketaux.com/v1/news/all"

# Network timeout (seconds) so a stalled request can't hang the agents,
# mirroring the EDGAR / FRED clients.
REQUEST_TIMEOUT = 30


class MarketauxNotConfiguredError(VendorNotConfiguredError):
    """MARKETAUX_API_KEY unset or rejected as invalid.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """


class MarketauxRateLimitError(VendorRateLimitError):
    """Marketaux usage/rate limit exceeded (HTTP 402/429); router tries the next vendor."""


def get_api_key() -> str:
    """Retrieve the Marketaux API key from the environment."""
    key = os.getenv("MARKETAUX_API_KEY")
    if not key:
        raise MarketauxNotConfiguredError(
            "MARKETAUX_API_KEY environment variable is not set."
        )
    return key


def _iso(date_str: str, end_of_day: bool = False) -> str:
    """Convert a ``yyyy-mm-dd`` date to the ISO timestamp Marketaux expects."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%Y-%m-%dT23:59:59" if end_of_day else "%Y-%m-%dT00:00:00")


def _request(path: str, params: dict) -> dict:
    """GET the Marketaux endpoint, injecting the api token; classify failure by behavior.

    ``path`` is carried for seam-uniformity with the edgar/fred clients so the
    test can ``mock.patch.object(marketaux, "_request", ...)``. Throttling (HTTP
    402/429) and JSON ``error`` objects are mapped to the typed vendor errors so
    the router can skip to the next vendor; an unrecognised JSON error raises a
    plain ValueError that ``get_news`` self-stringifies.
    """
    api_params = {**params, "api_token": get_api_key()}
    response = requests.get(path, params=api_params, timeout=REQUEST_TIMEOUT)
    if response.status_code in (402, 429):
        raise MarketauxRateLimitError(
            f"Marketaux rate/usage limit: HTTP {response.status_code}."
        )
    response.raise_for_status()
    data = response.json()
    err = data.get("error")
    if err:
        code = (err.get("code") or "").lower()
        msg = err.get("message", str(err))
        if "limit" in code or "rate" in code:
            raise MarketauxRateLimitError(f"Marketaux limit: {msg}")
        if "token" in code or "auth" in code:
            raise MarketauxNotConfiguredError(f"Marketaux key invalid: {msg}")
        raise ValueError(f"Marketaux error: {msg}")
    return data


def _entity_sentiment(article: dict, symbol: str) -> str | None:
    """Return the queried symbol's sentiment score (e.g. "+0.42"), or None.

    Filters ``entities[]`` to the requested symbol so a co-mentioned ticker's
    score is never mis-attributed to the one under analysis.
    """
    for ent in article.get("entities", []):
        if (ent.get("symbol") or "").upper() == symbol.upper():
            score = ent.get("sentiment_score")
            if score is not None:
                return f"{score:+.2f}"
    return None


def get_news(ticker, start_date, end_date) -> str:
    """Retrieve news for a ticker over a date window as a formatted Markdown report.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL"). The user's ticker stays in
            the report header; the canonical symbol is used for the query.
        start_date: Start date in yyyy-mm-dd format (inclusive).
        end_date: End date in yyyy-mm-dd format (inclusive).

    Returns:
        Formatted Markdown string of news articles with per-entity sentiment.

    Raises:
        NoMarketDataError: zero articles in the window — the router treats this
            as fall-through so the next configured news vendor is tried instead
            of ending the chain on a success-shaped "no news" string (FIND-7).
        MarketauxNotConfiguredError / MarketauxRateLimitError: re-raised so the
            router can skip to the next configured vendor. Other exceptions are
            self-stringified into an "Error fetching news ..." message.
    """
    # Query Marketaux with the canonical symbol, like every other vendor path —
    # a raw broker/forex/crypto alias otherwise silently returns no news. Keep
    # the user's ticker in the report header.
    canonical = normalize_symbol(ticker)
    resolved = "" if canonical == ticker else f" (resolved to {canonical})"
    try:
        data = _request(
            API_URL,
            {
                "symbols": canonical,
                # Server-side window keeps the query look-ahead safe.
                "published_after": _iso(start_date),
                "published_before": _iso(end_date, end_of_day=True),
                "language": "en",
                "filter_entities": "true",
                "limit": 50,
            },
        )
        articles = data.get("data", [])
        if not articles:
            # Zero articles is "no coverage", not a soft success: raise the router's
            # fall-through error so the next configured news vendor (yfinance) is
            # tried, instead of returning a success-shaped "no news" string that
            # ends the chain on thin coverage (FIND-7). Distinct from an API error,
            # which keeps self-stringifying via the ``except Exception`` below.
            raise NoMarketDataError(
                ticker, canonical, f"no articles between {start_date} and {end_date}"
            )

        news_str = ""
        for article in articles:
            title = article.get("title", "No title")
            source = article.get("source", "Unknown")
            summary = article.get("description") or article.get("snippet") or ""
            link = article.get("url", "")
            sentiment = _entity_sentiment(article, canonical)

            news_str += f"### {title} (source: {source})\n"
            if sentiment is not None:
                news_str += f"Sentiment ({canonical}): {sentiment}\n"
            if summary:
                news_str += f"{summary}\n"
            if link:
                news_str += f"Link: {link}\n"
            news_str += "\n"

        return f"## {ticker}{resolved} News, from {start_date} to {end_date}:\n\n{news_str}"

    except (MarketauxNotConfiguredError, MarketauxRateLimitError, NoMarketDataError):
        raise  # let the router skip to the next vendor / surface
    except Exception as e:
        return f"Error fetching news for {ticker}: {str(e)}"
