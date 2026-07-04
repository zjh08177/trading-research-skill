"""Offline tests for scripts/vendors/marketaux_news.py (seam: marketaux._request)."""
import json
import os

import pytest

import marketaux_news
from tradingagents.dataflows import marketaux
from tradingagents.dataflows.marketaux import (
    MarketauxNotConfiguredError,
    MarketauxRateLimitError,
)

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "vendors",
    "marketaux_nvda_5articles.json",
)


@pytest.fixture
def payload():
    with open(FIXTURE) as f:
        return json.load(f)


def run_main(monkeypatch, capsys, argv, response=None, exc=None):
    calls = []

    def fake_request(path, params):
        calls.append((path, params))
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(marketaux, "_request", fake_request)
    code = marketaux_news.main(argv)
    return code, capsys.readouterr(), calls


def test_happy_path(monkeypatch, capsys, payload):
    code, out, _ = run_main(
        monkeypatch, capsys,
        ["--ticker", "NVDA", "--asof", "2026-07-01", "--days", "7"],
        response=payload,
    )
    assert code == 0
    lines = out.out.strip().splitlines()
    assert len(lines) == 1  # exactly one line of compact JSON
    facts = json.loads(lines[0])
    assert set(facts) == {"P5.headlines"}
    f = facts["P5.headlines"]
    assert set(f) == {"v", "unit", "asof", "src"}
    assert f["unit"] == "articles"
    assert f["asof"] == "2026-07-01"
    assert f["src"] == "marketaux"
    assert f["v"] is not None
    rows = f["v"]
    assert len(rows) == 5
    # newest first
    dates = [r["published_at"] for r in rows]
    assert dates == sorted(dates, reverse=True)
    # full row verbatim: url/source/published_at values, not just key presence
    assert rows[0] == {
        "title": "Chips rally on export news",
        "source": "wireB",
        "published_at": "2026-06-29T09:30:00.000000Z",
        "url": "https://example.com/a2",
        "sentiment": 0.1,
    }
    by_title = {r["title"]: r for r in rows}
    # mis-attribution guard: NVDA's score (listed after AAPL, lowercase symbol),
    # not first-entity AAPL's
    assert by_title["NVDA mid-week wrap"]["sentiment"] == 0.42
    # no entity match / null score -> sentiment null
    assert by_title["Sector overview, no ticker calls"]["sentiment"] is None
    assert by_title["NVDA supplier note"]["sentiment"] is None
    for r in rows:
        assert set(r) == {"title", "source", "published_at", "url", "sentiment"}


def test_limit_caps_headlines(monkeypatch, capsys, payload):
    code, out, _ = run_main(
        monkeypatch, capsys,
        ["--ticker", "NVDA", "--asof", "2026-07-01", "--limit", "3"],
        response=payload,
    )
    assert code == 0
    rows = json.loads(out.out)["P5.headlines"]["v"]
    assert len(rows) == 3
    assert rows[0]["published_at"].startswith("2026-06-29")


def test_zero_articles_exit_3(monkeypatch, capsys):
    with pytest.raises(SystemExit) as ei:
        run_main(
            monkeypatch, capsys,
            ["--ticker", "NVDA", "--asof", "2026-07-01", "--days", "7"],
            response={"data": []},
        )
    assert ei.value.code == 3
    out = capsys.readouterr()
    assert out.out == ""  # nothing on stdout
    assert "NVDA" in out.err
    assert "2026-06-24" in out.err and "2026-07-01" in out.err


def test_missing_key_exit_2(monkeypatch, capsys):
    with pytest.raises(SystemExit) as ei:
        run_main(
            monkeypatch, capsys, ["--ticker", "NVDA"],
            exc=MarketauxNotConfiguredError(
                "MARKETAUX_API_KEY environment variable is not set."
            ),
        )
    assert ei.value.code == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "MARKETAUX_API_KEY" in out.err


def test_rate_limit_exit_4(monkeypatch, capsys):
    with pytest.raises(SystemExit) as ei:
        run_main(
            monkeypatch, capsys, ["--ticker", "NVDA"],
            exc=MarketauxRateLimitError("Marketaux rate/usage limit: HTTP 402."),
        )
    assert ei.value.code == 4
    out = capsys.readouterr()
    assert out.out == ""
    assert "limit" in out.err


def test_other_error_exit_1(monkeypatch, capsys):
    import requests

    with pytest.raises(SystemExit) as ei:
        run_main(
            monkeypatch, capsys, ["--ticker", "NVDA"],
            exc=requests.ConnectionError("boom"),
        )
    assert ei.value.code == 1
    assert capsys.readouterr().out == ""


def test_alias_ticker_normalized(monkeypatch, capsys, payload):
    _, _, calls = run_main(
        monkeypatch, capsys,
        ["--ticker", "gold", "--asof", "2026-07-01"],
        response=payload,
    )
    assert calls[0][1]["symbols"] == "GC=F"  # normalize_symbol("gold")


def test_window_math(monkeypatch, capsys, payload):
    _, _, calls = run_main(
        monkeypatch, capsys,
        ["--ticker", "NVDA", "--asof", "2026-07-01", "--days", "7"],
        response=payload,
    )
    path, params = calls[0]
    assert path == marketaux.API_URL
    assert params["published_after"] == marketaux._iso("2026-06-24")
    assert params["published_before"] == marketaux._iso("2026-07-01", end_of_day=True)
    assert params["language"] == "en"
    assert params["filter_entities"] == "true"
    assert params["limit"] == 10
