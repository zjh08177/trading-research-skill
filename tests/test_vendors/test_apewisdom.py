"""Hermetic tests for the apewisdom vendor CLI (P6 reddit_crowding source)."""
import pytest

import apewisdom


ROW = {"rank": 32, "ticker": "MRVL", "name": "Marvell", "mentions": 19,
       "upvotes": 46, "rank_24h_ago": 62, "mentions_24h_ago": 10}


def test_found_on_first_page_stops_scanning():
    pages = {1: [ROW, {"ticker": "NVDA"}], 2: [{"ticker": "AMD"}]}
    calls = []

    def fetch(page):
        calls.append(page)
        return pages.get(page, [])

    out = apewisdom.build_payload("mrvl", "2026-07-11", fetch=fetch)
    assert out["_apewisdom"]["row"] == ROW
    assert calls == [1]


def test_scans_pages_until_found():
    pages = {1: [{"ticker": "NVDA"}], 2: [ROW]}
    out = apewisdom.build_payload("MRVL", "2026-07-11",
                                  fetch=lambda p: pages.get(p, []))
    assert out["_apewisdom"]["row"] == ROW
    assert out["_apewisdom"]["scanned_ranks"] == 2


def test_not_ranked_is_valid_not_error():
    pages = {1: [{"ticker": "NVDA"}] * 100, 2: [{"ticker": "AMD"}] * 100,
             3: [{"ticker": "TSLA"}] * 100}
    out = apewisdom.build_payload("MRVL", "2026-07-11",
                                  fetch=lambda p: pages.get(p, []))
    assert out["_apewisdom"]["row"] is None
    assert out["_apewisdom"]["scanned_ranks"] == 300


def test_empty_leaderboard_raises():
    with pytest.raises(LookupError):
        apewisdom.build_payload("MRVL", "2026-07-11", fetch=lambda p: [])
