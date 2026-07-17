"""Offline tests for scripts/vendors/uw_bars.py — the UW-sourced P1/P2 CLI.

The sole network seam is ``_uw_common.get_json``; these tests monkeypatch it with
canned ``ohlc/1d`` payloads (three market_time rows per day) so no socket opens.
They pin the behaviors that make the Schwab sunset safe: regular-session filter,
consolidated full-day volume, PIT ``<= asof`` drop, and vendor exit codes.
"""
import math
from datetime import datetime, timedelta

import pytest

import _uw_common as uw
import uw_bars

ALL_KEYS = {
    "P1.price", "P1.chg_pct_1d", "P1.high_52w", "P1.low_52w", "P1.avg_vol_20d",
    "P2.sma20", "P2.sma50", "P2.sma200", "P2.rsi14", "P2.macd", "P2.macd_signal",
    "P2.atr14", "P2.atr14_pct", "P2.sigma30",
}


def make_ohlc(n, end="2026-06-30"):
    """n consecutive-calendar-day dates, each with pr/po/r rows. The regular (r)
    row carries the consolidated full-day `volume`; pr/po carry decoys so a test
    fails loudly if the CLI ever stops filtering to market_time == 'r'."""
    end_d = datetime.strptime(end, "%Y-%m-%d")
    rows = []
    for i in range(n):
        day = (end_d - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
        close = 100.0 + (i % 37) * 0.5 + i * 0.01
        full_vol = 1_000_000 + i * 137
        rows.append({"date": day, "market_time": "pr", "open": close, "high": close,
                     "low": close, "close": close, "volume": 5, "total_volume": 5})
        rows.append({"date": day, "market_time": "po", "open": close, "high": close,
                     "low": close, "close": close, "volume": 7, "total_volume": 7})
        rows.append({"date": day, "market_time": "r", "open": close, "high": close * 1.01,
                     "low": close * 0.99, "close": close, "volume": full_vol,
                     "total_volume": full_vol - 11_000})
    return rows


def patch(monkeypatch, rows, status=200):
    monkeypatch.setattr(uw, "get_json", lambda path, params=None: (status, {"data": rows}))
    monkeypatch.setattr(uw_bars.uw, "get_json", lambda path, params=None: (status, {"data": rows}))


def test_full_pack_and_src_stamp(monkeypatch):
    patch(monkeypatch, make_ohlc(260))
    facts = uw_bars.build_facts(uw_bars.fetch_frame("AAPL", "2026-06-30"), "2026-06-30", "AAPL")
    assert ALL_KEYS <= set(facts), ALL_KEYS - set(facts)
    assert all(v["src"] == "uw" for v in facts.values())
    for v in facts.values():
        assert math.isfinite(v["v"]) if isinstance(v["v"], float) else True


def test_regular_session_filter_and_full_day_volume(monkeypatch):
    # avg_vol_20d must reflect the regular-row `volume` (consolidated full day),
    # NOT total_volume and NOT the pr/po decoys.
    rows = make_ohlc(30)
    patch(monkeypatch, rows)
    facts = uw_bars.build_facts(uw_bars.fetch_frame("AAPL", "2026-06-30"), "2026-06-30", "AAPL")
    reg = [r for r in rows if r["market_time"] == "r"]
    expected = sum(r["volume"] for r in reg[-20:]) / 20.0
    assert facts["P1.avg_vol_20d"]["v"] == pytest.approx(expected)


def test_pit_drop_excludes_future_leak(monkeypatch):
    # end_date can leak one day forward (UTC rollover); the <= asof drop must
    # exclude it, so the last bar is exactly asof.
    rows = make_ohlc(40, end="2026-07-01")  # includes a 2026-07-01 row past a 06-30 asof
    patch(monkeypatch, rows)
    facts = uw_bars.build_facts(uw_bars.fetch_frame("AAPL", "2026-06-30"), "2026-06-30", "AAPL")
    assert facts["P1.price"]["asof"] == "2026-06-30"


def test_no_regular_rows_exits_3(monkeypatch):
    only_pre = [{"date": "2026-06-30", "market_time": "pr", "open": 1, "high": 1,
                 "low": 1, "close": 1, "volume": 1}]
    patch(monkeypatch, only_pre)
    with pytest.raises(SystemExit) as e:
        uw_bars.fetch_frame("AAPL", "2026-06-30")
    assert e.value.code == 3


def test_auth_and_ratelimit_exit_codes(monkeypatch):
    monkeypatch.setattr(uw_bars.uw, "get_json", lambda p, params=None: (401, {"error": "x"}))
    with pytest.raises(SystemExit) as e:
        uw_bars.fetch_frame("AAPL", "2026-06-30")
    assert e.value.code == 2
    monkeypatch.setattr(uw_bars.uw, "get_json", lambda p, params=None: (429, {"error": "x"}))
    with pytest.raises(SystemExit) as e:
        uw_bars.fetch_frame("AAPL", "2026-06-30")
    assert e.value.code == 4


def test_forming_current_bar_dropped(monkeypatch):
    # The newest regular date without a `po` sibling is a still-forming intraday
    # candle -> excluded so it never leaks as a settled bar. Older po-less rows
    # are untouched (guard only targets the max date).
    rows = make_ohlc(30, end="2026-06-29")            # all settled (pr/po/r each)
    rows.append({"date": "2026-06-30", "market_time": "r", "open": 9, "high": 9,
                 "low": 9, "close": 999.0, "volume": 123})  # forming: r, no po
    patch(monkeypatch, rows)
    facts = uw_bars.build_facts(uw_bars.fetch_frame("AAPL", "2026-06-30"), "2026-06-30", "AAPL")
    # 999.0 forming close must NOT become P1.price; last settled is 2026-06-29.
    assert facts["P1.price"]["asof"] == "2026-06-29"
    assert facts["P1.price"]["v"] != 999.0


def test_settled_last_bar_with_po_kept(monkeypatch):
    rows = make_ohlc(30, end="2026-06-30")            # 06-30 has a po row -> settled
    patch(monkeypatch, rows)
    facts = uw_bars.build_facts(uw_bars.fetch_frame("AAPL", "2026-06-30"), "2026-06-30", "AAPL")
    assert facts["P1.price"]["asof"] == "2026-06-30"


def test_null_volume_fails_loud(monkeypatch):
    rows = make_ohlc(30)
    rows[-1]["volume"] = None                          # malformed regular row
    patch(monkeypatch, rows)
    with pytest.raises(SystemExit) as e:
        uw_bars.fetch_frame("AAPL", "2026-06-30")
    assert e.value.code == 1


def test_build_facts_all_before_asof_returns_none():
    import pandas as pd
    df = pd.DataFrame([{"Date": "2026-07-05", "Open": 1.0, "High": 1.0,
                        "Low": 1.0, "Close": 1.0, "Volume": 1.0}])
    assert uw_bars.build_facts(df, "2026-06-30", "AAPL") is None
