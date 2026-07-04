"""Schwab daily-bars CLI: emits P1 (quote-derived) + P2 (indicator) facts as one JSON line."""
import argparse
import math
import sys
from datetime import datetime, timedelta, timezone

from _common import fact, emit, die  # must run before any tradingagents import

import pandas as pd

from tradingagents.dataflows import schwab
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

LOOKBACK_DAYS = 550
SRC = "schwab"


def build_facts(df, asof, ticker=""):
    """P1+P2 facts from a Date/Open/High/Low/Close/Volume frame; bars after asof dropped."""
    df = df[df["Date"] <= asof]
    if len(df) == 0:
        raise NoMarketDataError(ticker or "?", None, "no bars on or before %s" % asof)
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    bar_date = str(df["Date"].iloc[-1])
    n = len(df)
    facts = {}

    def put(key, v, unit):
        v = float(v)
        if math.isfinite(v):
            facts[key] = fact(v, unit, bar_date, SRC)

    put("P1.price", close.iloc[-1], "USD")
    if n >= 2:
        put("P1.chg_pct_1d", (close.iloc[-1] / close.iloc[-2] - 1.0) * 100.0, "pct")
    put("P1.high_52w", high.tail(252).max(), "USD")
    put("P1.low_52w", low.tail(252).min(), "USD")
    if n >= 20:
        put("P1.avg_vol_20d", vol.tail(20).mean(), "shares")
    for w in (20, 50, 200):
        if n >= w:
            put("P2.sma%d" % w, close.tail(w).mean(), "USD")
    if n >= 15:
        delta = close.diff()
        avg_up = delta.clip(lower=0.0).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
        avg_down = (-delta).clip(lower=0.0).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
        rsi = 100.0 if avg_down == 0 else 100.0 - 100.0 / (1.0 + avg_up / avg_down)
        put("P2.rsi14", rsi, "index")
        prev = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev).abs(), (low - prev).abs()], axis=1
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
        put("P2.atr14", atr, "USD")
        put("P2.atr14_pct", atr / close.iloc[-1] * 100.0, "pct")
    if n >= 26:
        macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        put("P2.macd", macd.iloc[-1], "USD")
        put("P2.macd_signal", macd.ewm(span=9, adjust=False).mean().iloc[-1], "USD")
    if n >= 31:
        put("P2.sigma30", (close.pct_change() * 100.0).tail(30).std(), "pct")
    return facts


def main(argv):
    p = argparse.ArgumentParser(prog="schwab_bars")
    p.add_argument("--ticker", required=True)
    p.add_argument("--asof", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = p.parse_args(argv)
    try:
        start = (
            datetime.strptime(args.asof, "%Y-%m-%d") - timedelta(days=LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")
        env = schwab.SchwabEquityVendor.fetch(args.ticker, start, args.asof)
        facts = build_facts(env.data.to_dataframe(), args.asof, args.ticker)
    except VendorNotConfiguredError as e:  # incl. SchwabReauthRequiredError; before ValueError
        die(str(e), 2)
    except NoMarketDataError as e:
        die(str(e), 3)
    except VendorRateLimitError as e:
        die(str(e), 4)
    except Exception as e:
        die("%s: %s" % (type(e).__name__, e), 1)
    emit(facts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
