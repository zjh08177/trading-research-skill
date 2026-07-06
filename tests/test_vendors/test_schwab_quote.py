"""Offline tests for scripts/vendors/schwab_quote.py (seam: schwab._request)."""
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest
import requests

import schwab_quote
from tradingagents.dataflows.errors import (
    SchwabReauthRequiredError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "vendors" / "schwab_quote.py")
TODAY = datetime.now().date().isoformat()  # local basis, matches the CLI guard
TRADE_MS = int(datetime(2026, 7, 2, 20, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
TRADE_ISO = datetime.fromtimestamp(TRADE_MS / 1000, tz=timezone.utc).isoformat()
KEYS = {"P1.last", "P1.day_high", "P1.day_low", "P1.day_volume", "P1.is_realtime"}


def make_payload(sym="NVDA", last=100.0, realtime=True, high=101.0, low=99.0,
                 vol=1_000_000, trade_ms=TRADE_MS, drop=()):
    quote = {"lastPrice": last, "highPrice": high, "lowPrice": low,
             "totalVolume": vol, "tradeTime": trade_ms}
    for k in drop:
        quote.pop(k, None)
    return {sym: {"realtime": realtime, "quoteType": "NBBO", "symbol": sym, "quote": quote}}


def patch_payload(monkeypatch, payload):
    monkeypatch.setattr(schwab_quote.schwab, "_request", lambda path, params: payload)


def run_ok(capsys, argv):
    assert schwab_quote.main(argv) == 0
    out, _ = capsys.readouterr()
    return out


def run_fail(capsys, argv, code):
    with pytest.raises(SystemExit) as exc:
        schwab_quote.main(argv)
    assert exc.value.code == code
    out, err = capsys.readouterr()
    assert out == ""  # error paths print NOTHING to stdout
    return err


def test_happy_path(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload(last=123.45, high=125.0, low=120.0, vol=9_000_000))
    out = run_ok(capsys, ["--ticker", "NVDA"])
    assert out.count("\n") == 1 and out.endswith("\n")  # single-line JSON
    facts = json.loads(out)
    assert set(facts) == KEYS
    for f in facts.values():
        assert set(f) == {"v", "unit", "asof", "src"}
        assert f["v"] is not None
        assert f["src"] == "schwab"
        assert f["asof"] == TRADE_ISO  # the trade-time instant, not today
    assert facts["P1.last"]["v"] == pytest.approx(123.45)
    assert facts["P1.last"]["unit"] == "USD"
    assert facts["P1.day_high"]["v"] == pytest.approx(125.0)
    assert facts["P1.day_low"]["v"] == pytest.approx(120.0)
    assert facts["P1.day_volume"]["v"] == pytest.approx(9_000_000)
    assert facts["P1.day_volume"]["unit"] == "shares"
    assert facts["P1.is_realtime"]["v"] is True


def test_delayed_flag_still_emits(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload(realtime=False))
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA"]))
    assert facts["P1.is_realtime"]["v"] is False  # caller boxes DELAYED; CLI still emits
    assert "P1.last" in facts


def test_lookahead_guard_refuses_past_asof(capsys):
    # Must NOT reach the network — guard fires before fetch; no payload patched.
    err = run_fail(capsys, ["--ticker", "NVDA", "--asof", "2020-01-01"], 3)
    assert "current-day" in err and "2020-01-01" in err and "settled bars" in err


def test_nonpadded_past_asof_refused(capsys):
    # Parsed as a date (2020-01-01), not lexically — a non-zero-padded past date
    # must still be refused (the string-compare look-ahead bug).
    run_fail(capsys, ["--ticker", "NVDA", "--asof", "2020-1-1"], 3)


def test_future_asof_refused(capsys):
    run_fail(capsys, ["--ticker", "NVDA", "--asof", "2099-01-01"], 3)


def test_malformed_asof_exit_2(capsys):
    err = run_fail(capsys, ["--ticker", "NVDA", "--asof", "not-a-date"], 2)
    assert "invalid --asof" in err


def test_asof_today_proceeds(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload())
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA", "--asof", TODAY]))
    assert facts["P1.last"]["v"] == pytest.approx(100.0)


def test_day_fields_omitted_when_absent(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload(drop=("highPrice", "lowPrice", "totalVolume")))
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA"]))
    assert set(facts) == {"P1.last", "P1.is_realtime"}  # omitted, never null


def test_no_quote_exit_3(monkeypatch, capsys):
    patch_payload(monkeypatch, {"NVDA": {"realtime": True, "quote": {}}})  # no lastPrice
    err = run_fail(capsys, ["--ticker", "NVDA"], 3)
    assert "NVDA" in err


def test_no_trade_time_fails_loud_exit_3(monkeypatch, capsys):
    # lastPrice present but no tradeTime/quoteTime -> no honest as-of -> fail loud,
    # never stamp the price with a fabricated now().
    patch_payload(monkeypatch, make_payload(drop=("tradeTime",)))
    err = run_fail(capsys, ["--ticker", "NVDA"], 3)
    assert "trade-time" in err


def test_missing_symbol_entry_exit_3(monkeypatch, capsys):
    patch_payload(monkeypatch, {})  # symbol absent from payload
    run_fail(capsys, ["--ticker", "NVDA"], 3)


def test_auth_fail_exit_2(monkeypatch, capsys):
    msg = "SCHWAB_ACCESS_TOKEN environment variable is not set."

    def boom(*a, **k):
        raise VendorNotConfiguredError(msg)

    monkeypatch.setattr(schwab_quote.schwab.SchwabQuoteVendor, "fetch", boom)
    err = run_fail(capsys, ["--ticker", "NVDA"], 2)
    assert msg in err


def test_reauth_exit_2_verbatim(monkeypatch, capsys):
    msg = "Schwab refresh token expired or within 24h of expiry; re-run the OAuth flow."

    def boom(*a, **k):
        raise SchwabReauthRequiredError(msg)

    monkeypatch.setattr(schwab_quote.schwab.SchwabQuoteVendor, "fetch", boom)
    err = run_fail(capsys, ["--ticker", "NVDA"], 2)
    assert err.strip() == msg


def test_rate_limit_exit_4(monkeypatch, capsys):
    def boom(path, params):
        raise VendorRateLimitError("Schwab rate limit: HTTP 429.")

    monkeypatch.setattr(schwab_quote.schwab, "_request", boom)
    run_fail(capsys, ["--ticker", "NVDA"], 4)


def test_http_flake_exit_1(monkeypatch, capsys):
    def boom(path, params):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(schwab_quote.schwab, "_request", boom)
    run_fail(capsys, ["--ticker", "NVDA"], 1)


def test_subprocess_smoke_no_creds(tmp_path):
    env = dict(os.environ)
    env["SCHWAB_TOKEN_PATH"] = str(tmp_path / "absent.json")  # no token store
    env["SCHWAB_ACCESS_TOKEN"] = ""  # falsy -> VendorNotConfiguredError before any socket
    r = subprocess.run(
        [sys.executable, SCRIPT, "--ticker", "AAOI"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 2
    assert r.stdout == ""
    assert r.stderr.strip()
