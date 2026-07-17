"""Offline tests for scripts/vendors/finnhub_oracle.py (seam: FinnhubMarketOracle.quote)."""
import datetime
import json

import finnhub_oracle as mod

TODAY = datetime.date.today().isoformat()
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def patch_quote(monkeypatch, fn):
    monkeypatch.setattr(mod.FinnhubMarketOracle, "quote", fn)


def test_happy_path_current_day(monkeypatch, capsys):
    patch_quote(monkeypatch, lambda self, t, asof: {"open": 10.0, "high": 11.0,
                                                     "low": 9.0, "close": 10.5})
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY])
    out = capsys.readouterr()
    assert code == 0
    payload = json.loads(out.out)
    assert payload == {"P1.px_finnhub_oob": {"v": 10.5, "unit": "USD",
                                             "asof": TODAY, "src": "finnhub"}}


def test_default_asof_is_today(monkeypatch, capsys):
    patch_quote(monkeypatch, lambda self, t, asof: {"close": 22.2})
    code = mod.main(["--ticker", "NVDA"])
    out = capsys.readouterr()
    assert code == 0
    assert json.loads(out.out)["P1.px_finnhub_oob"]["asof"] == TODAY


def test_past_asof_refused_no_call(monkeypatch, capsys):
    def must_not_call(self, t, asof):
        raise AssertionError("finnhub called for a past as_of (look-ahead)")
    patch_quote(monkeypatch, must_not_call)
    code = mod.main(["--ticker", "NVDA", "--asof", YESTERDAY])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""
    assert "current-day only" in out.err


def test_future_asof_refused(monkeypatch, capsys):
    future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    patch_quote(monkeypatch, lambda self, t, asof: (_ for _ in ()).throw(
        AssertionError("must not call")))
    code = mod.main(["--ticker", "NVDA", "--asof", future])
    assert code == 3


def test_missing_close_exit_3(monkeypatch, capsys):
    patch_quote(monkeypatch, lambda self, t, asof: {"open": 10.0})
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY])
    out = capsys.readouterr()
    assert code == 3
    assert out.out == ""


def test_missing_finnhub_key_runtimeerror_exit_2(monkeypatch, capsys):
    def raiser(self, t, asof):
        raise RuntimeError("FINNHUB_API_KEY not set")
    patch_quote(monkeypatch, raiser)
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY])
    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "FINNHUB_API_KEY" in out.err


def test_http_error_exit_1(monkeypatch, capsys):
    def raiser(self, t, asof):
        raise ValueError("boom")
    patch_quote(monkeypatch, raiser)
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY])
    out = capsys.readouterr()
    assert code == 1
    assert out.out == ""


def test_malformed_asof_exit_2(monkeypatch, capsys):
    code = mod.main(["--ticker", "NVDA", "--asof", "2026/07/02"])
    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "invalid --asof" in out.err


def test_smoke_subprocess_unpatchable_seam():
    import os
    import subprocess
    import sys as _sys
    env = {**os.environ, "FINNHUB_API_KEY": ""}
    r = subprocess.run(
        [_sys.executable, mod.__file__, "--ticker", "NVDA", "--asof", TODAY],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 2
    assert r.stdout == ""
    assert "FINNHUB_API_KEY" in r.stderr
