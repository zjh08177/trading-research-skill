"""Unusual Whales daily-bars CLI: emits P1 (quote-derived) + P2 (indicator)
facts as one JSON line — the UW-sourced replacement for ``schwab_bars.py``.

Fetches UW ``/api/stock/{ticker}/ohlc/1d`` and keeps ONLY the regular-session
rows (``market_time == "r"``): that endpoint returns three rows per date
(premarket ``pr`` / regular ``r`` / postmarket ``po``), and the regular row's
``volume`` field is the consolidated full-day share volume (verified: it equals
``stock-state.total_volume``). The resulting Date/OHLCV frame is fed through the
SAME indicator math ``schwab_bars.build_facts`` uses, so every P1/P2 value is
computed identically — only the bar SOURCE differs. Facts stamp ``src=uw``.

PIT / ``--asof``: UW ``end_date`` can leak ~1 day forward (documented UTC
rollover), so it is NOT a strict cutoff. The hard guard is the ``<= asof`` bar
drop inside ``build_facts`` — identical to ``schwab_bars`` — which removes any
leaked future bar. A back-dated ``--asof`` therefore stays look-ahead-safe.

Exit codes mirror the vendor taxonomy: 0 ok, 2 auth/config (missing key / 401),
3 no data (404 / empty after filter), 4 rate-limit (429), 1 other.
"""
import argparse
import math
from datetime import datetime, timezone

import pandas as pd

import _uw_common as uw

SRC = "uw"
# 2Y of regular-session bars (~500 trading days) comfortably exceeds the 252
# needed for 52-week extremes and the 200 for SMA200, and stays well under the
# endpoint's 2,500-element cap (2Y * ~252 * 3 rows/day ≈ 1,500).
TIMEFRAME = "2Y"


def build_facts(df, asof, ticker=""):
    """P1+P2 facts from a Date/Open/High/Low/Close/Volume frame; bars after asof
    dropped. Indicator math is a verbatim parity copy of ``schwab_bars.build_facts``
    (same EWM windows, same guards) so UW and Schwab produce identical values;
    only ``SRC`` differs. Returns ``None`` when no bar survives the ``<= asof`` drop
    (the caller maps that to exit 3)."""
    df = df[df["Date"] <= asof]
    if len(df) == 0:
        return None
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
            facts[key] = uw.fact(v, unit, bar_date, SRC)

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


def fetch_frame(ticker, asof):
    """Regular-session daily bars up to (and incl.) ``asof`` as a Date/OHLCV frame.

    Sole network seam is ``uw.get_json``. Maps UW statuses to vendor exit codes
    via ``uw.die`` (process-fatal) so a caller never sees a partial frame.
    """
    status, body = uw.get_json(
        "/api/stock/%s/ohlc/1d" % ticker, {"timeframe": TIMEFRAME, "end_date": asof}
    )
    if status == 401:
        uw.die("UW auth failed (401): check key in %s" % uw.CREDS_PATH, 2)
    if status == 429:
        uw.die("UW rate limited (429) on ohlc/%s" % ticker, 4)
    if status == 404:
        uw.die("UW no data (404) for %s" % ticker, 3)
    if status != 200:
        uw.die("UW HTTP %s on ohlc/%s: %s" % (status, ticker, str(body)[:160]), 1)
    rows = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(rows, list):
        uw.die("UW malformed ohlc payload for %s" % ticker, 1)
    reg = [r for r in rows if r.get("market_time") == "r"]
    if not reg:
        uw.die("UW returned no regular-session bars for %s" % ticker, 3)
    reg = _drop_forming_last_bar(reg, rows)
    if not reg:
        uw.die("UW returned no settled regular-session bars for %s" % ticker, 3)
    frame = pd.DataFrame([_row_to_bar(r, ticker) for r in reg])
    return frame.sort_values("Date").reset_index(drop=True)


def _drop_forming_last_bar(reg, rows):
    """Exclude the current day's still-forming regular candle.

    A daily-bar endpoint exposes today's regular candle mid-session as an
    in-progress bar; treating it as settled would corrupt P1.price/chg% and every
    P2 indicator during market hours. UW splits each date into pr/po/r rows and a
    postmarket (`po`) row only appears once the regular session has closed, so a
    date WITHOUT a `po` sibling is not yet settled. Applied only to the newest
    regular date (the sole bar that can still be forming) so a rare `po`-less
    historical day is never dropped. Clock-free by design (the codebase avoids
    weekday/holiday math)."""
    po_dates = {r.get("date") for r in rows if r.get("market_time") == "po"}
    latest = max(r["date"] for r in reg)
    if latest not in po_dates:
        return [r for r in reg if r["date"] != latest]
    return reg


def _row_to_bar(r, ticker):
    # A present-but-null `volume` is a malformed row — fail loud rather than
    # fabricate a 0-volume observation (which would understate avg_vol_20d and
    # P0 relative volume). A genuine 0 (e.g. a halted day) is preserved.
    vol = r.get("volume")
    if vol is None:
        uw.die("UW ohlc row for %s missing volume on %s" % (ticker, r.get("date")), 1)
    return {
        "Date": r["date"],
        "Open": float(r["open"]),
        "High": float(r["high"]),
        "Low": float(r["low"]),
        "Close": float(r["close"]),
        # Regular-row `volume` is the regular-session consolidated volume
        # (matches Schwab's needExtendedHoursData=False; postmarket is the `po` row).
        "Volume": float(vol),
    }


def main(argv):
    p = argparse.ArgumentParser(prog="uw_bars")
    p.add_argument("--ticker", required=True)
    p.add_argument("--asof", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = p.parse_args(argv)
    ticker = args.ticker.upper()
    frame = fetch_frame(ticker, args.asof)
    facts = build_facts(frame, args.asof, ticker)
    if facts is None:
        uw.die("no bars on or before %s for %s" % (args.asof, ticker), 3)
    uw.emit(facts)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
