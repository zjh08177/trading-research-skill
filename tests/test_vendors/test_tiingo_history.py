"""Offline tests for scripts/vendors/tiingo_history.py."""
import json
import urllib.error

import tiingo_history as mod

ROWS = [
    {"date": "2026-01-01T00:00:00.000Z", "close": 10.0, "adjClose": 9.0, "volume": 100},
    {"date": "2026-01-02T00:00:00.000Z", "close": 11.0, "adjClose": 10.0, "volume": 200},
    {"date": "2026-01-05T00:00:00.000Z", "close": 12.0, "adjClose": 11.0, "volume": 300},
]


def patch_fetch(monkeypatch, rows):
    monkeypatch.setattr(mod, "fetch_history", lambda ticker, start: rows)


def test_happy_path_filters_to_asof(monkeypatch, capsys):
    patch_fetch(monkeypatch, ROWS)
    code = mod.main(["--ticker", "SOXL", "--asof", "2026-01-02"])
    out = capsys.readouterr()
    assert code == 0
    payload = json.loads(out.out)
    assert payload["ticker"] == "SOXL"
    assert [b["date"] for b in payload["bars"]] == ["2026-01-01", "2026-01-02"]
    assert payload["bars"][0]["adjClose"] == 9.0


def test_empty_rows_exit_3(monkeypatch, capsys):
    patch_fetch(monkeypatch, [])
    code = mod.main(["--ticker", "SOXL", "--asof", "2026-01-02"])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""
    assert "SOXL" in out.err


def test_all_rows_after_asof_exit_3(monkeypatch, capsys):
    patch_fetch(monkeypatch, ROWS)
    code = mod.main(["--ticker", "SOXL", "--asof", "2025-12-31"])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""


def test_missing_key_exit_2(monkeypatch, capsys):
    def raiser(ticker, start):
        raise RuntimeError("TIINGO_API_KEY not set")
    monkeypatch.setattr(mod, "fetch_history", raiser)
    code = mod.main(["--ticker", "SOXL", "--asof", "2026-01-02"])
    out = capsys.readouterr()
    assert code == 2
    assert "TIINGO_API_KEY" in out.err


def test_http_error_exit_1(monkeypatch, capsys):
    def raiser(ticker, start):
        raise urllib.error.HTTPError("https://api.tiingo.com", 404, "Not Found", None, None)
    monkeypatch.setattr(mod, "fetch_history", raiser)
    code = mod.main(["--ticker", "NOPE", "--asof", "2026-01-02"])
    assert code == 1
    assert capsys.readouterr().out == ""


def test_malformed_asof_exit_2(monkeypatch, capsys):
    patch_fetch(monkeypatch, ROWS)
    code = mod.main(["--ticker", "SOXL", "--asof", "2026/01/02"])
    out = capsys.readouterr()
    assert code == 2
    assert "invalid --asof" in out.err


def test_default_start_date_used_when_omitted(monkeypatch, capsys):
    captured = {}

    def fake_fetch(ticker, start):
        captured["start"] = start
        return ROWS
    monkeypatch.setattr(mod, "fetch_history", fake_fetch)
    mod.main(["--ticker", "SOXL", "--asof", "2026-01-02"])
    assert captured["start"] == mod.DEFAULT_START
