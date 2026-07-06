"""Tests for monitor_invalidations.py — the pure trigger-evaluation logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import monitor_invalidations as mon  # noqa: E402


ENTRY = {"ticker": "T", "kind": "equity",
         "downside": {"level": 90.0, "action": "Exit", "basis": "SMA200"},
         "upside": {"level": 110.0, "action": "Add", "basis": "SMA50"}}


def test_downside_fires_at_or_below():
    f = mon.evaluate(ENTRY, 85.0)
    assert len(f) == 1 and f[0]["dir"] == "▼" and f[0]["action"] == "Exit" and f[0]["fired"]


def test_upside_fires_at_or_above():
    f = mon.evaluate(ENTRY, 115.0)
    assert len(f) == 1 and f[0]["dir"] == "▲" and f[0]["action"] == "Add"


def test_within_band_fires_nothing():
    assert mon.evaluate(ENTRY, 100.0) == []


def test_exact_level_fires():
    assert mon.evaluate(ENTRY, 90.0)[0]["dir"] == "▼"   # <= boundary
    assert mon.evaluate(ENTRY, 110.0)[0]["dir"] == "▲"  # >= boundary


def test_missing_price_reports_unavailable():
    f = mon.evaluate(ENTRY, None)
    assert f[0]["action"] == "PRICE UNAVAILABLE" and not f[0]["fired"]


def test_one_sided_registry_ok():
    e = {"ticker": "X", "kind": "crypto", "downside": {"level": 50.0, "action": "Sell"}, "upside": None}
    assert mon.evaluate(e, 40.0)[0]["action"] == "Sell"
    assert mon.evaluate(e, 60.0) == []


def test_load_registry_skips_malformed(tmp_path):
    import json
    (tmp_path / "AAA.json").write_text(json.dumps(
        {"ticker": "AAA", "kind": "equity", "asof": "2026-07-05", "spot": 100.0}))
    # a raw 56-levels.json hand-dropped here (no ticker envelope) — must be skipped,
    # not crash the whole scan (the 07-06 UNH break).
    (tmp_path / "raw.json").write_text(json.dumps(
        {"spot": 425.0, "downside": {"level": 410.0}, "upside": None, "derived": False}))
    reg, malformed = mon.load_registry(str(tmp_path))
    assert [e["ticker"] for e in reg] == ["AAA"]
    assert malformed == ["raw.json"]


def test_held_from_holdings():
    dump = {"holdings": [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": None}, {}]}
    assert mon.held_from_holdings(dump) == {"AAA", "BBB"}


def test_filter_to_held_drops_unheld_keeps_crypto():
    reg = [{"ticker": "AAA", "kind": "equity"},      # held
           {"ticker": "UNH", "kind": "equity"},      # ad-hoc, NOT held
           {"ticker": "BTC", "kind": "crypto"}]      # crypto: kept regardless
    kept, dropped = mon.filter_to_held(reg, {"AAA"})
    assert [e["ticker"] for e in kept] == ["AAA", "BTC"]
    assert dropped == ["UNH"]


def test_filter_none_scans_all():
    reg = [{"ticker": "AAA", "kind": "equity"}, {"ticker": "UNH", "kind": "equity"}]
    kept, dropped = mon.filter_to_held(reg, None)
    assert kept == reg and dropped == []
