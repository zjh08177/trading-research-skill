"""Offline tests for scripts/vendors/schwab_account.py + upstream SchwabAccountVendor.

Seam: schwab._request (patched to return canned /accounts payloads). Import order
matters — importing schwab_account runs _common, which puts UPSTREAM on sys.path,
so the `tradingagents` import below resolves.
"""
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

import pytest
import requests

import schwab_account  # noqa: E402 — triggers _common path bootstrap
from tradingagents.dataflows import schwab
from tradingagents.dataflows.errors import (
    SchwabReauthRequiredError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "vendors" / "schwab_account.py")
TODAY = datetime.now().date().isoformat()


def acct(positions, liq):
    return {"securitiesAccount": {"currentBalances": {"liquidationValue": liq},
                                  "positions": positions}}


def pos(sym, longq=0.0, shortq=0.0, avg=0.0, mv=0.0, pl=0.0, asset="EQUITY"):
    return {"longQuantity": longq, "shortQuantity": shortq, "averagePrice": avg,
            "marketValue": mv, "longOpenProfitLoss": pl,
            "instrument": {"symbol": sym, "assetType": asset}}


def patch(monkeypatch, payload):
    monkeypatch.setattr(schwab, "_request", lambda path, params: payload)


# ---- Task 1: SchwabAccountVendor aggregate ----

def test_flat_returns_none(monkeypatch):
    patch(monkeypatch, [acct([pos("AAPL", 10, avg=100, mv=1100, pl=100)], liq=5000)])
    assert schwab.SchwabAccountVendor.fetch_position("NVDA") is None


def test_single_account_held(monkeypatch):
    patch(monkeypatch, [acct([pos("NVDA", 100, avg=100.0, mv=16000.0, pl=6000.0)], liq=32000.0)])
    p = schwab.SchwabAccountVendor.fetch_position("NVDA")
    assert p.qty == pytest.approx(100.0)
    assert p.avg_price == pytest.approx(100.0)
    assert p.market_value == pytest.approx(16000.0)
    assert p.unrealized_pl == pytest.approx(6000.0)
    assert p.unrealized_pl_pct == pytest.approx(60.0)   # 6000/(16000-6000)*100
    assert p.pct_of_book == pytest.approx(50.0)         # 16000/32000*100
    assert p.n_accounts == 1


def test_multi_account_aggregate(monkeypatch):
    patch(monkeypatch, [
        acct([pos("NVDA", 100, avg=100.0, mv=16000.0, pl=6000.0)], liq=20000.0),
        acct([pos("NVDA", 100, avg=140.0, mv=16000.0, pl=2000.0)], liq=20000.0),
    ])
    p = schwab.SchwabAccountVendor.fetch_position("NVDA")
    assert p.qty == pytest.approx(200.0)
    assert p.avg_price == pytest.approx(120.0)          # (100*100+100*140)/200
    assert p.market_value == pytest.approx(32000.0)
    assert p.unrealized_pl == pytest.approx(8000.0)
    assert p.pct_of_book == pytest.approx(80.0)         # 32000/40000*100
    assert p.n_accounts == 2


def test_option_position_excluded(monkeypatch):
    patch(monkeypatch, [acct([pos("NVDA", 5, mv=500, pl=50, asset="OPTION")], liq=5000)])
    assert schwab.SchwabAccountVendor.fetch_position("NVDA") is None


def test_lowercase_symbol_matches(monkeypatch):
    patch(monkeypatch, [acct([pos("NVDA", 10, avg=10, mv=110, pl=10)], liq=1000)])
    assert schwab.SchwabAccountVendor.fetch_position("nvda").qty == pytest.approx(10.0)


def test_zero_liq_pct_of_book_zero(monkeypatch):
    patch(monkeypatch, [acct([pos("NVDA", 10, avg=10, mv=110, pl=10)], liq=0.0)])
    assert schwab.SchwabAccountVendor.fetch_position("NVDA").pct_of_book == 0.0


def test_short_position_is_flat(monkeypatch):
    # v1 is long-only: a pure short leg reports flat, never zeroed $/pct facts.
    patch(monkeypatch, [acct([pos("NVDA", longq=0, shortq=100, avg=120, mv=-12000, pl=0)], liq=50000)])
    assert schwab.SchwabAccountVendor.fetch_position("NVDA") is None


def test_zero_qty_settling_position_is_flat(monkeypatch):
    # a longQuantity=0/shortQuantity=0 residual object must not flip held=true.
    patch(monkeypatch, [acct([pos("NVDA", longq=0, shortq=0, avg=0, mv=0, pl=0)], liq=50000)])
    assert schwab.SchwabAccountVendor.fetch_position("NVDA") is None


def test_long_plus_short_across_accounts_counts_long_only(monkeypatch):
    patch(monkeypatch, [
        acct([pos("NVDA", longq=100, avg=100.0, mv=16000.0, pl=6000.0)], liq=20000.0),
        acct([pos("NVDA", longq=0, shortq=50, avg=140.0, mv=-7000.0, pl=0)], liq=20000.0),
    ])
    p = schwab.SchwabAccountVendor.fetch_position("NVDA")
    assert p.qty == pytest.approx(100.0)          # long leg only; short leg excluded
    assert p.market_value == pytest.approx(16000.0)
    assert p.unrealized_pl == pytest.approx(6000.0)
    assert p.n_accounts == 1


# ---- Task 2: CLI ----

def cli_ok(capsys, argv):
    assert schwab_account.main(argv) == 0
    out, _ = capsys.readouterr()
    return json.loads(out)


def cli_fail(capsys, argv, code):
    with pytest.raises(SystemExit) as e:
        schwab_account.main(argv)
    assert e.value.code == code
    out, err = capsys.readouterr()
    assert out == ""
    return err


def patch_req(monkeypatch, payload):
    monkeypatch.setattr(schwab_account.schwab, "_request", lambda path, params: payload)


def test_cli_held_emits_h1(monkeypatch, capsys):
    patch_req(monkeypatch, [acct([pos("NVDA", 100, avg=100.0, mv=16000.0, pl=6000.0)], liq=32000.0)])
    f = cli_ok(capsys, ["--ticker", "NVDA"])
    assert f["H1.held"]["v"] is True
    assert f["H1.qty"]["v"] == pytest.approx(100.0) and f["H1.qty"]["unit"] == "shares"
    assert f["H1.pct_of_book"]["v"] == pytest.approx(50.0) and f["H1.pct_of_book"]["unit"] == "%"
    assert f["H1.unrealized_pl_pct"]["v"] == pytest.approx(60.0)
    assert f["H1.avg_price"]["unit"] == "USD"
    for v in f.values():
        assert set(v) == {"v", "unit", "asof", "src"} and v["src"] == "schwab"


def test_cli_flat_emits_held_false(monkeypatch, capsys):
    patch_req(monkeypatch, [acct([pos("AAPL", 10, mv=1000, pl=0)], liq=5000)])
    f = cli_ok(capsys, ["--ticker", "NVDA"])
    assert list(f) == ["H1.held"]
    assert f["H1.held"]["v"] is False


def test_cli_past_asof_exit_3(capsys):
    cli_fail(capsys, ["--ticker", "NVDA", "--asof", "2020-01-01"], 3)


def test_cli_nonpadded_past_asof_exit_3(capsys):
    cli_fail(capsys, ["--ticker", "NVDA", "--asof", "2020-1-1"], 3)


def test_cli_future_asof_exit_3(capsys):
    cli_fail(capsys, ["--ticker", "NVDA", "--asof", "2099-01-01"], 3)


def test_cli_malformed_asof_exit_2(capsys):
    err = cli_fail(capsys, ["--ticker", "NVDA", "--asof", "not-a-date"], 2)
    assert "invalid --asof" in err


def test_cli_asof_today_ok(monkeypatch, capsys):
    patch_req(monkeypatch, [acct([pos("NVDA", 1, avg=1, mv=2, pl=1)], liq=10)])
    assert cli_ok(capsys, ["--ticker", "NVDA", "--asof", TODAY])["H1.held"]["v"] is True


def test_cli_auth_exit_2(monkeypatch, capsys):
    def boom(*a, **k):
        raise VendorNotConfiguredError("no token")
    monkeypatch.setattr(schwab_account.schwab.SchwabAccountVendor, "fetch_position", boom)
    cli_fail(capsys, ["--ticker", "NVDA"], 2)


def test_cli_reauth_exit_2(monkeypatch, capsys):
    def boom(*a, **k):
        raise SchwabReauthRequiredError("reauth")
    monkeypatch.setattr(schwab_account.schwab.SchwabAccountVendor, "fetch_position", boom)
    cli_fail(capsys, ["--ticker", "NVDA"], 2)


def test_cli_rate_limit_exit_4(monkeypatch, capsys):
    def boom(path, params):
        raise VendorRateLimitError("429")
    monkeypatch.setattr(schwab_account.schwab, "_request", boom)
    cli_fail(capsys, ["--ticker", "NVDA"], 4)


def test_cli_flake_exit_1(monkeypatch, capsys):
    def boom(path, params):
        raise requests.ConnectionError("reset")
    monkeypatch.setattr(schwab_account.schwab, "_request", boom)
    cli_fail(capsys, ["--ticker", "NVDA"], 1)


def test_cli_subprocess_no_creds_exit_2(tmp_path):
    env = dict(os.environ)
    env["SCHWAB_TOKEN_PATH"] = str(tmp_path / "absent.json")
    env["SCHWAB_ACCESS_TOKEN"] = ""
    r = subprocess.run([sys.executable, SCRIPT, "--ticker", "AAOI"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 2 and r.stdout == "" and r.stderr.strip()


# ---- Task 3: read-only assertion ----

def test_no_order_or_mutation_path():
    vsrc = inspect.getsource(schwab.SchwabAccountVendor).lower()
    src = (vsrc + inspect.getsource(schwab_account)).lower()
    assert "/orders" not in src and "placeorder" not in src           # no order endpoint
    assert ".post(" not in src and ".put(" not in src and ".delete(" not in src  # no mutation verbs
    # The vendor's ONLY network egress is the audited GET seam _request; it never
    # touches `requests` directly, so it cannot bypass to a mutation verb.
    assert "requests." not in vsrc and "_request(" in vsrc
    req = inspect.getsource(schwab._request).lower()                  # and _request is GET-only
    assert "requests.get(" in req
    assert ".post(" not in req and ".put(" not in req and ".delete(" not in req
    assert "trader/v1/accounts" in inspect.getsource(schwab)
