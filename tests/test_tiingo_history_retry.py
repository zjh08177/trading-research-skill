"""Offline tests for tiingo_history.py retry-with-backoff + exit taxonomy.

No network: urllib.request.urlopen and time.sleep are monkeypatched on the
module under test. Mirrors the import-bootstrap style of test_qa_check.py."""
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vendors"))
import tiingo_history as th  # noqa: E402


def _http_error(code):
    return urllib.error.HTTPError(
        url="https://api.tiingo.com/x", code=code, msg=f"HTTP {code}",
        hdrs=None, fp=None)


class _FakeResp:
    """Context-manager stand-in for urlopen()'s response; json.load reads it."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _install(monkeypatch, side_effects):
    """side_effects: list consumed per urlopen call. An Exception instance is
    raised; anything else is treated as the JSON body to return. Returns the
    call-count list and the recorded sleep durations."""
    calls = []
    sleeps = []
    seq = iter(side_effects)

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        import json
        return _FakeResp(json.dumps(item).encode())

    monkeypatch.setenv("TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(th.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(th.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_T1_retries_429_twice_then_succeeds(monkeypatch):
    rows = [{"date": "2020-01-01", "close": 1},
            {"date": "2020-01-02", "close": 2},
            {"date": "2020-01-03", "close": 3}]
    calls, sleeps = _install(
        monkeypatch, [_http_error(429), _http_error(429), rows])
    out = th.fetch_history("X", "2000-01-01")
    assert out == rows
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_T2_all_429_raises_and_main_exits_4(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_http_error(429)] * 4)
    with pytest.raises(urllib.error.HTTPError):
        th.fetch_history("X", "2000-01-01")
    assert len(calls) == 4
    assert sleeps == [2.0, 4.0, 6.0]

    # main() maps a post-retry 429 to exit 4.
    calls2, sleeps2 = _install(monkeypatch, [_http_error(429)] * 4)
    assert th.main(["--ticker", "X", "--asof", "2026-07-22"]) == 4
    assert len(calls2) == 4


def test_T3_404_raises_immediately_no_sleep(monkeypatch):
    calls, sleeps = _install(monkeypatch, [_http_error(404)])
    with pytest.raises(urllib.error.HTTPError):
        th.fetch_history("X", "2000-01-01")
    assert len(calls) == 1
    assert sleeps == []


def test_T4_urlerror_exhausts_and_main_exits_1(monkeypatch):
    calls, sleeps = _install(
        monkeypatch, [urllib.error.URLError("timed out")] * 4)
    assert th.main(["--ticker", "X", "--asof", "2026-07-22"]) == 1
    assert len(calls) == 4


def test_T5_503_once_then_success(monkeypatch):
    rows = [{"date": "2020-01-01", "close": 1}]
    calls, sleeps = _install(monkeypatch, [_http_error(503), rows])
    out = th.fetch_history("X", "2000-01-01")
    assert out == rows
    assert len(calls) == 2
    assert sleeps == [2.0]
