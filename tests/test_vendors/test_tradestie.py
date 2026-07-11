"""Hermetic tests for the tradestie vendor CLI (P6 reddit_tone source).

All fetches are stubbed — no network. The replay strictly-prior-day guard
(ERD R9) is the load-bearing behavior here.
"""
import pytest

import tradestie


ROW = {"ticker": "MRVL", "sentiment": "Bullish", "sentiment_score": 0.21,
       "no_of_comments": 14}


def _fetch_factory(data_by_date):
    calls = []

    def fetch(day_iso):
        calls.append(day_iso)
        return data_by_date.get(day_iso, [])

    fetch.calls = calls
    return fetch


def test_live_returns_ticker_row_for_asof_day():
    fetch = _fetch_factory({"2026-07-10": [ROW, {"ticker": "NVDA"}]})
    out = tradestie.build_payload("mrvl", "2026-07-10", fetch=fetch)
    body = out["_tradestie"]
    assert body["date"] == "2026-07-10"
    assert body["row"] == ROW
    assert body["ticker"] == "MRVL"
    assert body["n_ranked"] == 2


def test_live_walks_back_over_empty_days():
    fetch = _fetch_factory({"2026-07-08": [ROW]})
    out = tradestie.build_payload("MRVL", "2026-07-10", fetch=fetch)
    assert out["_tradestie"]["date"] == "2026-07-08"
    assert fetch.calls == ["2026-07-10", "2026-07-09", "2026-07-08"]


def test_live_not_ranked_is_valid_not_error():
    fetch = _fetch_factory({"2026-07-10": [{"ticker": "NVDA"}]})
    out = tradestie.build_payload("MRVL", "2026-07-10", fetch=fetch)
    assert out["_tradestie"]["row"] is None
    assert out["_tradestie"]["n_ranked"] == 1


def test_replay_fetches_strictly_prior_day():
    fetch = _fetch_factory({"2026-07-10": [{"ticker": "X"}], "2026-07-09": [ROW]})
    out = tradestie.build_payload("MRVL", "2026-07-10", replay=True, fetch=fetch)
    assert out["_tradestie"]["date"] == "2026-07-09"
    assert "2026-07-10" not in fetch.calls  # never touches cutoff-day data


def test_replay_guard_rejects_cutoff_or_later_date():
    # A fetch stub that (wrongly) serves cutoff-day data must trip the assert.
    def fetch(day_iso):
        return [ROW]

    monkey = tradestie.resolve_leaderboard
    try:
        tradestie.resolve_leaderboard = lambda start, fetch=None: ("2026-07-10", [ROW])
        with pytest.raises(AssertionError):
            tradestie.build_payload("MRVL", "2026-07-10", replay=True, fetch=fetch)
    finally:
        tradestie.resolve_leaderboard = monkey


def test_no_data_within_lookback_raises():
    fetch = _fetch_factory({})
    with pytest.raises(LookupError):
        tradestie.build_payload("MRVL", "2026-07-10", fetch=fetch)
