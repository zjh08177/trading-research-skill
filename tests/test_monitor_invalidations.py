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
