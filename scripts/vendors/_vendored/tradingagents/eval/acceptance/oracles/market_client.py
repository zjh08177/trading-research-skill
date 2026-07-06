"""Independent market-data oracle (W4 T-ACC.1) — the LIVE D1 price source.

The live acceptance harness verifies a trace's numbers against sources it does
NOT itself produce (D1). Two out-of-band vendors, both DELIBERATELY disjoint from
the system ``data_vendors`` (schwab/yfinance/alpha_vantage/...):

* ``quote()`` → **Finnhub** ``/quote`` — same-day RAW o/h/l/c (+ prev-close ``pc``).
  Advisory secondary: ``/quote`` returns the live/current value and ignores as_of,
  so it is NOT the settled-close basis.
* ``bars()`` → **Tiingo** ``/tiingo/daily/<t>/prices`` — date-bounded settled EOD
  daily OHLCV. PRIMARY basis: DERIVED recompute (SMA/EMA) AND RAW-close verified
  against the settled EOD bar at the trace as_of (GB5 / M5). Finnhub's free
  ``stock/candle`` is premium-gated (HTTP 403), which is why bars moved to Tiingo.

Network is reached ONLY when a method is called with the matching key set; the
deterministic offline gates use ``FixtureOracle`` and never construct this client.
Stdlib ``urllib`` only — no import of any ``dataflows`` vendor or the router.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta

# Tiingo returns only the LATEST bar when no startDate is given; request a
# generous history so DERIVED recompute (SMA-200 needs ~200 trading days) and the
# as-of RAW lookup both have enough settled bars.
_TIINGO_LOOKBACK_DAYS = 500

#: Out-of-band RAW vendor id (Finnhub /quote). Asserted disjoint from the system
#: data vendors (not in ``VENDOR_METHODS['get_stock_data']`` nor configured).
ORACLE_MARKET_VENDOR = "finnhub"
#: Out-of-band bars/DERIVED vendor id (Tiingo daily). Also asserted disjoint.
ORACLE_BARS_VENDOR = "tiingo"

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_TIINGO_BASE = "https://api.tiingo.com/tiingo/daily"


class FinnhubMarketOracle:
    """Out-of-band market oracle: Finnhub RAW quote + Tiingo settled EOD bars.

    Live only — offline gates use ``FixtureOracle`` and never construct this.
    ``vendor_id`` reports the RAW (Finnhub) vendor; the bars vendor (Tiingo) is
    named by ``bars_vendor_id`` and both are asserted disjoint from the system
    data vendors.
    """

    vendor_id = ORACLE_MARKET_VENDOR
    bars_vendor_id = ORACLE_BARS_VENDOR

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = _FINNHUB_BASE,
        tiingo_key: str | None = None,
        tiingo_base: str = _TIINGO_BASE,
    ):
        self._api_key = api_key or os.environ.get("FINNHUB_API_KEY")
        self._base_url = base_url
        self._tiingo_key = tiingo_key or os.environ.get("TIINGO_API_KEY")
        self._tiingo_base = tiingo_base

    def _get(self, path: str, params: dict) -> dict:
        if not self._api_key:
            raise RuntimeError(
                "FINNHUB_API_KEY not set; the out-of-band market oracle is live-only "
                "(offline acceptance gates use FixtureOracle)."
            )
        query = urllib.parse.urlencode({**params, "token": self._api_key})
        url = f"{self._base_url}/{path}?{query}"
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - https only
            return json.loads(resp.read().decode())

    def quote(self, ticker: str, as_of: str | None) -> dict:
        """Same-day RAW o/h/l/c for ``ticker`` (Finnhub /quote; ignores as_of)."""
        raw = self._get("quote", {"symbol": ticker})
        return {
            "open": raw.get("o"),
            "high": raw.get("h"),
            "low": raw.get("l"),
            "close": raw.get("c"),
        }

    def bars(self, ticker: str, window: int | None = None) -> list[dict]:
        """Settled EOD daily OHLCV for ``ticker`` (Tiingo /tiingo/daily/<t>/prices).

        The PRIMARY out-of-band basis: DERIVED recompute + as-of RAW lookup. Each
        row carries an ISO ``date`` (``YYYY-MM-DD``, sliced from Tiingo's timestamp)
        so ``verify`` can select the settled bar at the trace as_of.
        """
        if not self._tiingo_key:
            raise RuntimeError(
                "TIINGO_API_KEY not set; the out-of-band bars oracle is live-only "
                "(offline acceptance gates use FixtureOracle)."
            )
        # Bound the request with a startDate — without it Tiingo returns only the
        # latest bar, starving DERIVED recompute + the as-of lookup (live-only path).
        start = (date.today() - timedelta(days=_TIINGO_LOOKBACK_DAYS)).isoformat()
        query = urllib.parse.urlencode({"token": self._tiingo_key, "startDate": start})
        url = f"{self._tiingo_base}/{ticker.lower()}/prices?{query}"
        with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - https only
            raw = json.loads(resp.read().decode()) or []
        rows = [
            {
                "date": str(r.get("date", ""))[:10],
                "open": r.get("open"),
                "high": r.get("high"),
                "low": r.get("low"),
                "close": r.get("close"),
                "volume": r.get("volume"),
            }
            for r in raw
        ]
        return rows[-window:] if window else rows

    def fundamental(self, ticker: str, metric_key: str, as_of: str | None) -> float | None:
        """A basic fundamental ratio for ``ticker`` (Finnhub metric endpoint)."""
        raw = self._get("stock/metric", {"symbol": ticker, "metric": "all"})
        return raw.get("metric", {}).get(metric_key)
