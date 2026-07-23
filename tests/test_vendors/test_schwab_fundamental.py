"""Offline tests for scripts/vendors/schwab_fundamental.py (seam: schwab._request).

This CLI is a READ-ONLY raw passthrough: it requests fields=quote,fundamental
on the same Schwab quotes endpoint SchwabQuoteVendor.fetch uses, but emits the
untouched `fundamental` block under one private key (`_schwab_fundamental`)
for a later distiller to turn into cited P3.* facts with proper units. It must
NOT call/modify SchwabQuoteVendor — it drives schwab._request directly.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import requests

import schwab_fundamental
from tradingagents.dataflows.errors import (
    SchwabReauthRequiredError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "vendors" / "schwab_fundamental.py")

FUNDAMENTAL = {
    "high52": 150.0,
    "low52": 80.0,
    "peRatio": 25.4,
    "divYield": 0.012,
    "epsTTM": 4.32,
}


_UNSET = object()


def make_payload(sym="NVDA", fundamental=_UNSET, include_fundamental_key=True, quote=None):
    entry = {"symbol": sym}
    if quote is not None:
        entry["quote"] = quote
    if include_fundamental_key:
        entry["fundamental"] = FUNDAMENTAL if fundamental is _UNSET else fundamental
    return {sym: entry}


def patch_payload(monkeypatch, payload, capture=None):
    def fake_request(path, params):
        if capture is not None:
            capture["path"] = path
            capture["params"] = params
        return payload
    monkeypatch.setattr(schwab_fundamental.schwab, "_request", fake_request)


def run_ok(capsys, argv):
    assert schwab_fundamental.main(argv) == 0
    out, _ = capsys.readouterr()
    return out


def run_fail(capsys, argv, code):
    with pytest.raises(SystemExit) as exc:
        schwab_fundamental.main(argv)
    assert exc.value.code == code
    out, err = capsys.readouterr()
    assert out == ""  # error paths print NOTHING to stdout
    return err


def test_happy_path_emits_raw_fundamental_block(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload())
    out = run_ok(capsys, ["--ticker", "NVDA"])
    assert out.count("\n") == 1 and out.endswith("\n")  # single-line JSON
    parsed = json.loads(out)
    assert set(parsed) == {"_schwab_fundamental"}
    assert parsed["_schwab_fundamental"] == FUNDAMENTAL  # raw, untouched


def test_requests_fields_quote_and_fundamental(monkeypatch, capsys):
    cap = {}
    patch_payload(monkeypatch, make_payload(), capture=cap)
    run_ok(capsys, ["--ticker", "nvda"])
    assert cap["params"]["fields"] == "quote,fundamental"
    assert cap["params"]["symbols"] == "NVDA"  # upcased like schwab_quote
    assert cap["path"] == schwab_fundamental.schwab.SCHWAB_QUOTES_PATH


def test_missing_fundamental_key_emits_empty_dict_no_crash(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload(include_fundamental_key=False))
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA"]))
    assert facts == {"_schwab_fundamental": {}}


def test_fundamental_null_in_payload_emits_empty_dict(monkeypatch, capsys):
    patch_payload(monkeypatch, make_payload(fundamental=None))
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA"]))
    assert facts == {"_schwab_fundamental": {}}


def test_missing_symbol_entry_exit_3(monkeypatch, capsys):
    patch_payload(monkeypatch, {})  # symbol entirely absent from payload -> no data
    err = run_fail(capsys, ["--ticker", "NVDA"], 3)
    assert "NVDA" in err


def test_auth_fail_exit_2(monkeypatch, capsys):
    msg = "SCHWAB_ACCESS_TOKEN environment variable is not set."

    def boom(path, params):
        raise VendorNotConfiguredError(msg)

    monkeypatch.setattr(schwab_fundamental.schwab, "_request", boom)
    err = run_fail(capsys, ["--ticker", "NVDA"], 2)
    assert msg in err


def test_reauth_exit_2_verbatim(monkeypatch, capsys):
    msg = "Schwab refresh token expired or within 24h of expiry; re-run the OAuth flow."

    def boom(path, params):
        raise SchwabReauthRequiredError(msg)

    monkeypatch.setattr(schwab_fundamental.schwab, "_request", boom)
    err = run_fail(capsys, ["--ticker", "NVDA"], 2)
    assert err.strip() == msg


def test_rate_limit_exit_4(monkeypatch, capsys):
    def boom(path, params):
        raise VendorRateLimitError("Schwab rate limit: HTTP 429.")

    monkeypatch.setattr(schwab_fundamental.schwab, "_request", boom)
    run_fail(capsys, ["--ticker", "NVDA"], 4)


def test_http_flake_exit_1(monkeypatch, capsys):
    def boom(path, params):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(schwab_fundamental.schwab, "_request", boom)
    run_fail(capsys, ["--ticker", "NVDA"], 1)


def test_does_not_touch_schwab_quote_vendor(monkeypatch, capsys):
    # Regression guard for the "do not modify SchwabQuoteVendor" constraint:
    # calling this CLI must never invoke SchwabQuoteVendor.fetch.
    def boom(*a, **k):
        raise AssertionError("schwab_fundamental must not call SchwabQuoteVendor.fetch")

    monkeypatch.setattr(schwab_fundamental.schwab.SchwabQuoteVendor, "fetch", boom)
    patch_payload(monkeypatch, make_payload())
    run_ok(capsys, ["--ticker", "NVDA"])


def test_subprocess_smoke_no_creds(tmp_path, monkeypatch):
    env = dict()
    import os
    env.update(os.environ)
    env["SCHWAB_TOKEN_PATH"] = str(tmp_path / "absent.json")  # no token store
    env["SCHWAB_ACCESS_TOKEN"] = ""  # falsy -> VendorNotConfiguredError before any socket
    r = subprocess.run(
        [sys.executable, SCRIPT, "--ticker", "AAOI"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 2
    assert r.stdout == ""
    assert r.stderr.strip()
