"""Offline tests for scripts/vendors/tiingo_oracle.py (seam: FinnhubMarketOracle.bars)."""
import datetime
import io
import json
import urllib.error

import pytest

import tiingo_oracle as mod

TODAY = datetime.date.today().isoformat()

ROWS = [
    {"date": "2026-06-29", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100},
    {"date": "2026-06-30", "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.25, "volume": 200},
    {"date": "2026-07-01", "open": 11.0, "high": 13.0, "low": 11.0, "close": 12.75, "volume": 300},
]


def patch_bars(monkeypatch, fn):
    monkeypatch.setattr(mod.FinnhubMarketOracle, "bars", fn)


def run(monkeypatch, capsys, argv, bars_fn):
    patch_bars(monkeypatch, bars_fn)
    code = mod.main(argv)
    return code, capsys.readouterr()


def assert_fact_shape(payload):
    for v in payload.values():
        assert set(v) == {"v", "unit", "asof", "src"}
        assert v["v"] is not None


def test_happy_path_last_bar_lte_asof(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["--ticker", "NVDA", "--asof", "2026-07-02"],
                    lambda self, t, window=None: ROWS)
    assert code == 0
    lines = out.out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert_fact_shape(payload)
    f = payload["P1.px_close_oob"]
    assert f == {"v": 12.75, "unit": "USD", "asof": "2026-07-01", "src": "tiingo"}


def test_asof_older_than_last_bar_selects_earlier(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["--ticker", "NVDA", "--asof", "2026-06-30"],
                    lambda self, t, window=None: ROWS)
    assert code == 0
    f = json.loads(out.out)["P1.px_close_oob"]
    assert f["v"] == 11.25
    assert f["asof"] == "2026-06-30"


def test_empty_rows_exit_3_no_stdout(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["--ticker", "NVDA", "--asof", "2026-07-02"],
                    lambda self, t, window=None: [])
    assert code == 3
    assert out.out == ""
    assert "NVDA" in out.err


def test_no_bar_on_or_before_asof_exit_3(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["--ticker", "NVDA", "--asof", "2026-06-01"],
                    lambda self, t, window=None: ROWS)
    assert code == 3
    assert out.out == ""


def test_missing_tiingo_key_runtimeerror_exit_2(monkeypatch, capsys):
    def raiser(self, t, window=None):
        raise RuntimeError("TIINGO_API_KEY not set; the out-of-band bars oracle is live-only")
    code, out = run(monkeypatch, capsys, ["--ticker", "NVDA"], raiser)
    assert code == 2
    assert out.out == ""
    assert "TIINGO_API_KEY" in out.err


def test_http_404_exit_1_no_stdout(monkeypatch, capsys):
    def raiser(self, t, window=None):
        raise urllib.error.HTTPError("https://api.tiingo.com", 404, "Not Found", None, None)
    code, out = run(monkeypatch, capsys, ["--ticker", "NOPE"], raiser)
    assert code == 1
    assert out.out == ""
    assert out.err.strip() != ""


def test_constructor_never_requires_finnhub_key(monkeypatch, capsys):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    code, out = run(monkeypatch, capsys, ["--ticker", "NVDA", "--asof", "2026-07-02"],
                    lambda self, t, window=None: ROWS)
    assert code == 0
    assert json.loads(out.out)["P1.px_close_oob"]["v"] == 12.75


def test_build_facts_skips_null_close():
    rows = ROWS + [{"date": "2026-07-02", "close": None}]
    facts = mod.build_facts(rows, "2026-07-02")
    assert facts["P1.px_close_oob"]["asof"] == "2026-07-01"


def test_live_flag_adds_px_last_oob(monkeypatch, capsys):
    patch_bars(monkeypatch, lambda self, t, window=None: ROWS)
    monkeypatch.setattr(mod, "fetch_iex_last", lambda t: (12.80, "2026-07-02T20:00:00+00:00"))
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY, "--live"])
    out = capsys.readouterr()
    assert code == 0
    payload = json.loads(out.out)
    assert payload["P1.px_close_oob"]["src"] == "tiingo"  # settled emission unchanged
    assert payload["P1.px_last_oob"] == {
        "v": 12.80, "unit": "USD", "asof": "2026-07-02T20:00:00+00:00", "src": "tiingo"}


def test_live_flag_past_asof_skips_iex(monkeypatch, capsys):
    patch_bars(monkeypatch, lambda self, t, window=None: ROWS)

    def must_not_call(t):
        raise AssertionError("fetch_iex_last called for a past as_of (look-ahead)")

    monkeypatch.setattr(mod, "fetch_iex_last", must_not_call)
    code = mod.main(["--ticker", "NVDA", "--asof", "2026-06-30", "--live"])
    out = capsys.readouterr()
    assert code == 0
    payload = json.loads(out.out)
    assert payload["P1.px_close_oob"]["v"] == 11.25  # settled still emitted
    assert "P1.px_last_oob" not in payload  # live cross-check skipped


def test_live_iex_failure_is_best_effort(monkeypatch, capsys):
    patch_bars(monkeypatch, lambda self, t, window=None: ROWS)

    def boom(t):
        raise ValueError("empty /iex response")

    monkeypatch.setattr(mod, "fetch_iex_last", boom)
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY, "--live"])
    out = capsys.readouterr()
    assert code == 0  # settled emission stands
    payload = json.loads(out.out)
    assert "P1.px_close_oob" in payload
    assert "P1.px_last_oob" not in payload
    assert "iex cross-check unavailable" in out.err


def test_no_live_flag_omits_px_last_oob(monkeypatch, capsys):
    patch_bars(monkeypatch, lambda self, t, window=None: ROWS)
    monkeypatch.setattr(mod, "fetch_iex_last",
                        lambda t: (_ for _ in ()).throw(AssertionError("called without --live")))
    code = mod.main(["--ticker", "NVDA", "--asof", TODAY])
    assert code == 0
    assert "P1.px_last_oob" not in json.loads(capsys.readouterr().out)


def test_malformed_asof_exit_2(monkeypatch, capsys):
    patch_bars(monkeypatch, lambda self, t, window=None: ROWS)
    code = mod.main(["--ticker", "NVDA", "--asof", "2026/07/02"])
    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "invalid --asof" in out.err


def _fake_urlopen(rows):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    return lambda url, timeout=15: _Resp(json.dumps(rows).encode())


def test_fetch_iex_last_prefers_tngolast(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "k")
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        _fake_urlopen([{"tngoLast": 55.5, "last": 55.4,
                                        "timestamp": "2026-07-02T20:00:00+00:00"}]))
    px, ts = mod.fetch_iex_last("NVDA")
    assert px == 55.5 and ts == "2026-07-02T20:00:00+00:00"


def test_fetch_iex_last_falls_back_to_last(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "k")
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        _fake_urlopen([{"tngoLast": None, "last": 55.4, "timestamp": None}]))
    px, ts = mod.fetch_iex_last("NVDA")
    assert px == 55.4 and ts is None


def test_fetch_iex_last_missing_price_raises(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "k")
    monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen([{"tngoLast": None, "last": None}]))
    with pytest.raises(ValueError):
        mod.fetch_iex_last("NVDA")


def test_fetch_iex_last_no_key_raises(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        mod.fetch_iex_last("NVDA")


def test_smoke_subprocess_unpatchable_seam():
    import os
    import subprocess
    import sys as _sys
    # TIINGO_API_KEY="" is falsy and load_dotenv(override=False) cannot replace
    # it, so bars() raises RuntimeError offline -> exit 2, stdout empty.
    env = {**os.environ, "TIINGO_API_KEY": ""}
    r = subprocess.run(
        [_sys.executable, mod.__file__, "--ticker", "NVDA"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 2
    assert r.stdout == ""
    assert "TIINGO_API_KEY" in r.stderr
