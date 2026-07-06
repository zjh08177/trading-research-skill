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


def test_load_holdings_dump_raw_and_envelope(tmp_path):
    import json
    raw = {"holdings": [{"symbol": "AAA"}], "total_book": 1.0}
    env = {"kind": "holdings-snapshot", "schema": 1, "vendor": raw}
    (tmp_path / "raw.json").write_text(json.dumps(raw))
    (tmp_path / "env.json").write_text(json.dumps(env))
    assert mon.load_holdings_dump(str(tmp_path / "raw.json")) == raw
    assert mon.load_holdings_dump(str(tmp_path / "env.json")) == raw  # envelope unwraps


def _levels_dir(tmp_path):
    """A one-name registry that fires downside at price 85."""
    import json
    d = tmp_path / "levels"
    d.mkdir()
    (d / "T.json").write_text(json.dumps(
        {"ticker": "T", "kind": "equity", "asof": "2026-07-06", "spot": 100.0,
         "downside": {"level": 90.0, "action": "Exit", "basis": "SMA200"}, "upside": None}))
    return d


def test_sidecar_dumps_fired_and_md_byte_identical(tmp_path, monkeypatch):
    import json
    d = _levels_dir(tmp_path)
    out_md = tmp_path / "monitor-2026-07-06.md"
    monkeypatch.setattr(mon, "equity_price", lambda t: 85.0)   # fire downside
    monkeypatch.setattr(mon, "crypto_prices", lambda: {})
    rc = mon.main([str(d), str(out_md), "2026-07-06", "--all"])
    assert rc == 0
    sidecar = tmp_path / "monitor-2026-07-06.json"
    fired = json.loads(sidecar.read_text())
    assert [r["ticker"] for r in fired] == ["T"]
    assert fired[0]["action"] == "Exit" and fired[0]["fired"] is True
    # md carries the fired row; the sidecar is purely additive.
    assert "**Exit**" in out_md.read_text()


def test_sidecar_empty_when_nothing_fires(tmp_path, monkeypatch):
    import json
    d = _levels_dir(tmp_path)
    out_md = tmp_path / "monitor-2026-07-06.md"
    monkeypatch.setattr(mon, "equity_price", lambda t: 100.0)  # inside band
    monkeypatch.setattr(mon, "crypto_prices", lambda: {})
    mon.main([str(d), str(out_md), "2026-07-06", "--all"])
    assert json.loads((tmp_path / "monitor-2026-07-06.json").read_text()) == []


def test_holdings_arg_scopes_without_live_fetch(tmp_path, monkeypatch):
    import json
    d = _levels_dir(tmp_path)
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"vendor": {"holdings": [{"symbol": "OTHER"}]}}))
    # T is not in the snapshot → scoped out; fetch_held must NOT be called.
    monkeypatch.setattr(mon, "fetch_held", lambda: (_ for _ in ()).throw(AssertionError("live fetch")))
    monkeypatch.setattr(mon, "equity_price", lambda t: 85.0)
    monkeypatch.setattr(mon, "crypto_prices", lambda: {})
    out_md = tmp_path / "monitor-2026-07-06.md"
    mon.main([str(d), str(out_md), "2026-07-06", "--holdings", str(snap)])
    assert json.loads((tmp_path / "monitor-2026-07-06.json").read_text()) == []  # T scoped out


class _Proc:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_fetch_held_raises_on_import_error(monkeypatch):
    """Wrong-interpreter ModuleNotFoundError must be LOUD — a silent None here
    would fall back to the full registry and mask holdings-scoping being off."""
    import pytest
    monkeypatch.setattr(mon.subprocess, "run",
                        lambda *a, **k: _Proc(1, "", "ModuleNotFoundError: No module named 'snaptrade_client'"))
    with pytest.raises(RuntimeError):
        mon.fetch_held()


def test_fetch_held_falls_back_on_auth_failure(monkeypatch):
    """A non-import failure (auth / exit!=0) still returns None so the caller
    falls back to the full registry."""
    monkeypatch.setattr(mon.subprocess, "run",
                        lambda *a, **k: _Proc(1, "", "SnapTradeError: 401 unauthorized"))
    assert mon.fetch_held() is None
