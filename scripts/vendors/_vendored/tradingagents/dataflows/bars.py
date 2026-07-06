"""Thin OHLCV bars container shared by the equity vendors (W1 T1.3).

Both the fixture and Schwab equity vendors produce a :class:`Bars` and wrap it
in an :class:`~tradingagents.dataflows.envelope.Envelope`. From the SAME object
they render the two consumer shapes the codebase already expects:

* ``to_csv()`` — the 6-column ``Date,Open,High,Low,Close,Volume`` CSV the routed
  ``get_stock_data`` tool returns (header matches ``y_finance.py``).
* ``to_dataframe()`` — a capitalized-column DataFrame the verified market
  snapshot reads (``Date/Open/High/Low/Close/Volume``).

Keeping both renderings on one container is what lets a vendor stamp provenance
once, at the data boundary, before the data is stringified for the LLM.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from pydantic import BaseModel

_CSV_HEADER = "Date,Open,High,Low,Close,Volume"
_DF_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]


class Bars(BaseModel):
    """A symbol's daily OHLCV rows.

    ``rows`` is a list of ``{"date", "open", "high", "low", "close", "volume"}``
    dicts (date as ``YYYY-MM-DD``); the vendor builds it from its own response.
    """

    symbol: str
    rows: list[dict]

    def to_csv(self) -> str:
        """Render a 6-column CSV with the ``Date,Open,High,Low,Close,Volume`` header."""
        lines = [_CSV_HEADER]
        for r in self.rows:
            lines.append(
                f"{r['date']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}"
            )
        return "\n".join(lines) + "\n"

    def to_dataframe(self) -> pd.DataFrame:
        """Render a capitalized-column DataFrame in the shape the snapshot reads."""
        records = [
            {
                "Date": r["date"],
                "Open": r["open"],
                "High": r["high"],
                "Low": r["low"],
                "Close": r["close"],
                "Volume": r["volume"],
            }
            for r in self.rows
        ]
        return pd.DataFrame(records, columns=_DF_COLUMNS)


def render_stock_data_csv(bars: Bars, start_date: str, end_date: str) -> str:
    """Comment-header + 6-column CSV, matching ``y_finance.get_YFin_data_online``.

    Shared by both equity vendors so the routed ``get_stock_data`` contract emits
    the same shape regardless of vendor (a commented preamble the agent ignores,
    then the CSV body).
    """
    header = (
        f"# Stock data for {bars.symbol} from {start_date} to {end_date}\n"
        f"# Total records: {len(bars.rows)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + bars.to_csv()
