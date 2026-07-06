"""Schwab equity OHLCV vendor (W1 T1.3).

Live ``get_stock_data`` vendor over the Schwab Market Data ``pricehistory``
endpoint, returning a provenance-stamped :class:`Envelope` of :class:`Bars`.

LIVE-UNTESTABLE in W1: Schwab OAuth app provisioning (P1) is still pending, so
this module is exercised ONLY against a canned ``_request`` payload in the unit
tests. Live verification against the real endpoint is deferred to EC10. The
network seam is the module-level ``_request(path, params)`` -- patch it
(``mock.patch.object(schwab, "_request", ...)``) to drive the parser offline.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import requests
from pydantic import BaseModel

from .bars import Bars, render_stock_data_csv
from .envelope import Envelope, Provenance
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

# Schwab Market Data price-history path (candles: [{open,high,low,close,volume,datetime}]).
SCHWAB_PRICE_HISTORY_PATH = "https://api.schwabapi.com/marketdata/v1/pricehistory"
# Schwab Market Data quotes path (live snapshot: {SYM: {realtime, quoteType, quote{...}}}).
SCHWAB_QUOTES_PATH = "https://api.schwabapi.com/marketdata/v1/quotes"
REQUEST_TIMEOUT = 15


def _get_access_token() -> str:
    """Return the Schwab OAuth bearer token used by every Schwab data fetch.

    When an auto-refresh token store exists (``SCHWAB_TOKEN_PATH`` / default), mint
    or reuse the access token from it — so the data fetch consumes the SAME
    credential ``precheck_oauth`` validated and the 30-min auto-refresh actually
    happens on fetch. Otherwise fall back to the legacy static ``SCHWAB_ACCESS_TOKEN``
    env var. Raises ``VendorNotConfiguredError`` (incl. its ``SchwabReauthRequiredError``
    subclass) when no usable credential exists, so the router treats Schwab as
    unavailable rather than crashing the run.
    """
    from . import schwab_auth

    store_path = schwab_auth.resolve_token_path()
    if os.path.exists(store_path):
        return schwab_auth.get_access_token(now=datetime.now(timezone.utc))
    token = os.getenv("SCHWAB_ACCESS_TOKEN")
    if not token:
        raise VendorNotConfiguredError(
            "SCHWAB_ACCESS_TOKEN environment variable is not set."
        )
    return token


def precheck_oauth(config: dict, selected_analysts=(), now=None) -> None:
    """Fail loud at run start if Schwab is the configured vendor but its OAuth
    credential is missing or lapsed — BEFORE any LLM spend, instead of mid-run
    when a tool first hits the network.

    Two paths: with an auto-refresh token store (``schwab_token_path``) the 7-day
    refresh-token cadence is checked (``SchwabReauthRequiredError`` when expired or
    inside the re-auth band); otherwise the legacy static ``SCHWAB_ACCESS_TOKEN``
    must be present. Positive token-validity against the live endpoint still
    defers to EC10 (``-m live``).
    """
    vendors = config.get("data_vendors", {})
    schwab_equity = vendors.get("core_stock_apis") == "schwab"
    schwab_options = (
        "options" in tuple(selected_analysts)
        and vendors.get("options_chain") == "schwab"
    )
    if not (schwab_equity or schwab_options):
        return

    from . import schwab_auth
    from .errors import SchwabReauthRequiredError

    token_path = schwab_auth.resolve_token_path()
    if os.path.exists(token_path):
        # Auto-refresh path: guard the 7-day refresh-token cadence at run start
        # (24h ahead of expiry — stricter than the fetch path, which works until
        # the token is actually expired).
        store = schwab_auth.load_store(token_path)
        health = schwab_auth.refresh_health(store, now=now or datetime.now(timezone.utc))
        if health["needs_reauth_soon"]:
            raise SchwabReauthRequiredError(
                "Schwab refresh token expired or within 24h of expiry; re-run the OAuth flow."
            )
        return

    # Legacy/manual path: raises VendorNotConfiguredError when SCHWAB_ACCESS_TOKEN
    # is unset, before the pipeline spends on the LLM.
    _get_access_token()


def _epoch_ms(date_str: str) -> int:
    """``yyyy-mm-dd`` -> epoch milliseconds (UTC), the unit Schwab expects."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _price_history_params(symbol: str, start_date: str | None, end_date: str | None) -> dict:
    """Build daily price-history query params for the Schwab endpoint.

    ``periodType`` is always ``year`` so it stays compatible with
    ``frequencyType=daily`` — Schwab rejects daily candles under the default
    ``periodType=day`` (minute-only) with a 400. With an explicit start/end
    window Schwab honors the dates and ignores ``period``; without one,
    ``period=1`` requests the trailing year.
    """
    params: dict = {
        "symbol": symbol.upper(),
        "periodType": "year",
        "frequencyType": "daily",
        "frequency": 1,
        "needExtendedHoursData": False,
        "needPreviousClose": False,
    }
    if start_date:
        params["startDate"] = _epoch_ms(start_date)
    if end_date:
        # endDate (midnight) excludes that day's own daily bar, so a window
        # ending on the analysis date omits it — disagreeing with the verified
        # snapshot (which includes <= end_date) and tripping the G13 post-pass
        # ("core datum unresolved: close"). Add a day so end_date's bar is kept.
        params["endDate"] = _epoch_ms(end_date) + 86_400_000
    if not (start_date and end_date):
        params["period"] = 1
    return params


def _request(path: str, params: dict) -> dict:
    """GET the Schwab endpoint with the bearer token; classify failure by behavior.

    Module-level for seam-uniformity with the edgar/marketaux clients: the unit
    tests ``mock.patch.object(schwab, "_request", ...)`` to inject a canned
    pricehistory payload and never touch the network. The token is read FIRST so
    a missing-credentials run raises before any socket is opened.
    """
    token = _get_access_token()
    response = requests.get(
        path,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 429:
        raise VendorRateLimitError("Schwab rate limit: HTTP 429.")
    response.raise_for_status()
    return response.json()


class SchwabEquityVendor:
    """Parse the Schwab pricehistory response into a stamped ``Envelope[Bars]``."""

    endpoint = SCHWAB_PRICE_HISTORY_PATH

    @classmethod
    def fetch(
        cls,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Envelope[Bars]:
        """Fetch candles and wrap them with REAL provenance from the response.

        Raises ``NoMarketDataError`` when Schwab reports no candles.
        """
        payload = _request(cls.endpoint, _price_history_params(symbol, start_date, end_date))
        candles = payload.get("candles") or []
        if payload.get("empty") or not candles:
            raise NoMarketDataError(symbol, None, "Schwab returned no candles")

        rows = []
        for candle in candles:
            bar_dt = datetime.fromtimestamp(candle["datetime"] / 1000, tz=timezone.utc)
            rows.append(
                {
                    "date": bar_dt.strftime("%Y-%m-%d"),
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                }
            )
        bars = Bars(symbol=symbol.upper(), rows=rows)

        # vendor_as_of comes from the API (last candle's instant). Pricehistory
        # candles are settled EOD bars — never a live feed — so is_delayed is
        # always True. (The payload carries no freshness field to read.)
        vendor_as_of = datetime.fromtimestamp(candles[-1]["datetime"] / 1000, tz=timezone.utc)
        provenance = Provenance(
            vendor="schwab",
            endpoint=cls.endpoint,
            fetched_at=datetime.now(timezone.utc),
            vendor_as_of=vendor_as_of,
            is_delayed=True,
            source="SCHWAB",
        )
        return Envelope[Bars](data=bars, provenance=provenance)


class Quote(BaseModel):
    """A live Schwab quote snapshot: current last price + running-session context."""

    symbol: str
    last: float
    day_high: float | None = None
    day_low: float | None = None
    day_volume: float | None = None
    is_realtime: bool
    trade_time: datetime | None = None


class SchwabQuoteVendor:
    """Parse the Schwab quotes endpoint into a stamped ``Envelope[Quote]``.

    Unlike pricehistory (settled EOD candles), this returns the CURRENT last
    trade with the vendor's own trade-time — the day-D price a settled-bars-only
    path cannot supply intraday. ``is_delayed`` is derived from the real
    ``realtime`` entitlement flag, never a missing key.
    """

    endpoint = SCHWAB_QUOTES_PATH

    @classmethod
    def fetch(cls, symbol: str) -> Envelope[Quote]:
        """Fetch the live quote for one symbol; raise ``NoMarketDataError`` if absent."""
        sym = symbol.upper()
        payload = _request(cls.endpoint, {"symbols": sym, "fields": "quote"})
        entry = payload.get(sym) or {}
        q = entry.get("quote") or {}
        last = q.get("lastPrice")
        if last is None:
            raise NoMarketDataError(symbol, None, "Schwab returned no quote")
        trade_ms = q.get("tradeTime") or q.get("quoteTime")
        if not trade_ms:
            # No vendor timestamp = no honest as-of. Fail loud rather than
            # back-fill fetched_at (which envelope.py forbids: that fabricates
            # freshness). The caller then falls back to settled bars.
            raise NoMarketDataError(symbol, None, "Schwab quote carries no trade-time")
        trade_time = datetime.fromtimestamp(trade_ms / 1000, tz=timezone.utc)
        is_realtime = bool(entry.get("realtime"))
        quote = Quote(
            symbol=sym,
            last=last,
            day_high=q.get("highPrice"),
            day_low=q.get("lowPrice"),
            day_volume=q.get("totalVolume"),
            is_realtime=is_realtime,
            trade_time=trade_time,
        )
        provenance = Provenance(
            vendor="schwab",
            endpoint=cls.endpoint,
            fetched_at=datetime.now(timezone.utc),
            vendor_as_of=trade_time,
            is_delayed=not is_realtime,
            source="SCHWAB",
        )
        return Envelope[Quote](data=quote, provenance=provenance)


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Routed ``get_stock_data`` contract: CSV string for the requested symbol."""
    env = SchwabEquityVendor.fetch(symbol, start_date, end_date)
    return render_stock_data_csv(env.data, start_date, end_date)


def load_dataframe(symbol: str, curr_date: str) -> pd.DataFrame:
    """Capitalized-column DataFrame for the vendor-aware snapshot loader."""
    return SchwabEquityVendor.fetch(symbol).data.to_dataframe()


# Schwab Trader API accounts collection (read-only here: positions + balances).
# accountNumbers → hashValue is available for single-account queries but unused;
# aggregate-all needs one GET on this collection. No order/trade path exists.
SCHWAB_ACCOUNTS_PATH = "https://api.schwabapi.com/trader/v1/accounts"


class AggregatePosition(BaseModel):
    """One symbol's holding summed across all accounts. `_pct` in percent-points."""

    qty: float
    avg_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    pct_of_book: float
    n_accounts: int


class SchwabAccountVendor:
    """Aggregate the user's position in one symbol across every Schwab account.

    Read-only: a single GET on ``/accounts?fields=positions``. Returns ``None``
    when the symbol is not held long — a valid flat state, not an error.

    v1 scope is LONG equity/ETF holdings: options and any leg with
    ``longQuantity <= 0`` (short lots, zero-qty settling objects) are excluded, so
    a short or net-flat symbol reports flat rather than emitting zeroed $/`_pct`
    facts. `_pct` facts are percent-points so the report's ``8.0% [H1.pct_of_book]``
    passes adjacency QA.
    """

    endpoint = SCHWAB_ACCOUNTS_PATH

    @classmethod
    def fetch_position(cls, symbol: str) -> "AggregatePosition | None":
        sym = symbol.upper()
        payload = _request(cls.endpoint, {"fields": "positions"})
        total_liq = 0.0
        matches = []
        for acct in payload or []:
            sa = acct.get("securitiesAccount", {})
            total_liq += float(sa.get("currentBalances", {}).get("liquidationValue") or 0.0)
            for p in sa.get("positions", []):
                instr = p.get("instrument", {})
                if instr.get("symbol") != sym or instr.get("assetType") == "OPTION":
                    continue
                if (p.get("longQuantity") or 0.0) <= 0:  # exclude shorts + zero-qty lots (v1 = long)
                    continue
                matches.append(p)
        if not matches:
            return None
        long_qty = sum(p.get("longQuantity") or 0.0 for p in matches)
        market_value = sum(p.get("marketValue") or 0.0 for p in matches)
        unrealized_pl = sum(p.get("longOpenProfitLoss") or 0.0 for p in matches)
        avg_price = sum((p.get("averagePrice") or 0.0) * (p.get("longQuantity") or 0.0)
                        for p in matches) / long_qty
        cost = market_value - unrealized_pl
        return AggregatePosition(
            qty=long_qty,
            avg_price=avg_price,
            market_value=market_value,
            unrealized_pl=unrealized_pl,
            unrealized_pl_pct=(100.0 * unrealized_pl / cost) if cost else 0.0,
            pct_of_book=(100.0 * market_value / total_liq) if total_liq else 0.0,
            n_accounts=len(matches),
        )
