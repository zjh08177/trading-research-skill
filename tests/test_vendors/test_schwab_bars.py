"""Offline tests for scripts/vendors/schwab_bars.py (design test_plan schwab_bars cases 1-7 + smoke)."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import requests

import schwab_bars
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    SchwabReauthRequiredError,
    VendorNotConfiguredError,
)

SCRIPT = "/Users/bytedance/Work/sidekicks/tradingagents-workspace/trading-research-skill/scripts/vendors/schwab_bars.py"
END = datetime(2026, 6, 30, tzinfo=timezone.utc)
ALL_KEYS = {
    "P1.price", "P1.chg_pct_1d", "P1.high_52w", "P1.low_52w", "P1.avg_vol_20d",
    "P2.sma20", "P2.sma50", "P2.sma200", "P2.rsi14", "P2.macd", "P2.macd_signal",
    "P2.atr14", "P2.atr14_pct", "P2.sigma30",
}


def make_candles(n, end=END):
    """n synthetic consecutive-calendar-day bars ending on `end`, closed-form values."""
    out = []
    for i in range(n):
        day = end - timedelta(days=n - 1 - i)
        close = 100.0 + (i % 37) * 0.5 + i * 0.01
        out.append({
            "datetime": int(day.timestamp() * 1000),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000 + i * 137,
        })
    return out


def patch_payload(monkeypatch, candles):
    monkeypatch.setattr(
        schwab_bars.schwab, "_request", lambda path, params: {"candles": candles, "delayed": True}
    )


def run_ok(capsys, argv):
    assert schwab_bars.main(argv) == 0
    out, _ = capsys.readouterr()
    return out


def run_fail(capsys, argv, code):
    with pytest.raises(SystemExit) as exc:
        schwab_bars.main(argv)
    assert exc.value.code == code
    out, err = capsys.readouterr()
    assert out == ""  # error paths print NOTHING to stdout
    return err


def wilder(vals, alpha=1.0 / 14):
    a = vals[0]
    for v in vals[1:]:
        a += alpha * (v - a)
    return a


def ema(vals, span):
    alpha = 2.0 / (span + 1)
    a = vals[0]
    out = [a]
    for v in vals[1:]:
        a += alpha * (v - a)
        out.append(a)
    return out


def test_happy_path(monkeypatch, capsys):
    candles = make_candles(400)
    patch_payload(monkeypatch, candles)
    out = run_ok(capsys, ["--ticker", "NVDA", "--asof", "2026-07-02"])
    assert out.count("\n") == 1 and out.endswith("\n")  # single-line JSON
    facts = json.loads(out)
    assert set(facts) == ALL_KEYS
    for f in facts.values():  # cross-cutting shape checks
        assert set(f) == {"v", "unit", "asof", "src"}
        assert f["v"] is not None
        assert f["src"] == "schwab"
        assert f["asof"] == "2026-06-30"  # last bar date, not today
    c = [x["close"] for x in candles]
    h = [x["high"] for x in candles]
    lo = [x["low"] for x in candles]
    v = [x["volume"] for x in candles]
    assert facts["P1.price"]["v"] == pytest.approx(c[-1], abs=1e-6)
    assert facts["P1.chg_pct_1d"]["v"] == pytest.approx((c[-1] / c[-2] - 1) * 100, abs=1e-6)
    assert facts["P1.high_52w"]["v"] == pytest.approx(max(h[-252:]), abs=1e-6)
    assert facts["P1.low_52w"]["v"] == pytest.approx(min(lo[-252:]), abs=1e-6)
    assert facts["P1.avg_vol_20d"]["v"] == pytest.approx(sum(v[-20:]) / 20, abs=1e-3)
    for w in (20, 50, 200):
        assert facts["P2.sma%d" % w]["v"] == pytest.approx(sum(c[-w:]) / w, abs=1e-6)
    tr = [h[0] - lo[0]] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, 400)
    ]
    atr = wilder(tr)
    assert facts["P2.atr14"]["v"] == pytest.approx(atr, abs=1e-6)
    assert facts["P2.atr14_pct"]["v"] == pytest.approx(atr / c[-1] * 100, abs=1e-6)
    ups = [max(c[i] - c[i - 1], 0.0) for i in range(1, 400)]
    downs = [max(c[i - 1] - c[i], 0.0) for i in range(1, 400)]
    rsi = 100 - 100 / (1 + wilder(ups) / wilder(downs))
    assert facts["P2.rsi14"]["v"] == pytest.approx(rsi, abs=1e-6)
    macd = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    assert facts["P2.macd"]["v"] == pytest.approx(macd[-1], abs=1e-6)
    assert facts["P2.macd_signal"]["v"] == pytest.approx(ema(macd, 9)[-1], abs=1e-6)
    rets = [(c[i] / c[i - 1] - 1) * 100 for i in range(370, 400)]
    mean = sum(rets) / 30
    sigma = (sum((r - mean) ** 2 for r in rets) / 29) ** 0.5
    assert facts["P2.sigma30"]["v"] == pytest.approx(sigma, abs=1e-6)


def test_short_history_omits_sma200(monkeypatch, capsys):
    patch_payload(monkeypatch, make_candles(150))
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA", "--asof", "2026-07-02"]))
    assert "P2.sma200" not in facts  # OMITTED, never null
    assert set(facts) == ALL_KEYS - {"P2.sma200"}


def test_asof_windowing_excludes_later_bars(monkeypatch, capsys):
    candles = make_candles(400)  # ends 2026-06-30; last 10 bars are AFTER asof
    patch_payload(monkeypatch, candles)
    facts = json.loads(run_ok(capsys, ["--ticker", "NVDA", "--asof", "2026-06-20"]))
    c = [x["close"] for x in candles][:390]  # calendar-daily bars -> 390 bars <= 2026-06-20
    assert facts["P1.price"]["v"] == pytest.approx(c[-1], abs=1e-6)
    assert facts["P1.price"]["asof"] == "2026-06-20"
    assert facts["P2.sma20"]["v"] == pytest.approx(sum(c[-20:]) / 20, abs=1e-6)


def test_auth_fail_exit_2(monkeypatch, capsys):
    msg = "SCHWAB_ACCESS_TOKEN environment variable is not set."

    def boom(*a, **k):
        raise VendorNotConfiguredError(msg)

    monkeypatch.setattr(schwab_bars.schwab.SchwabEquityVendor, "fetch", boom)
    err = run_fail(capsys, ["--ticker", "NVDA"], 2)
    assert msg in err


def test_reauth_exit_2_verbatim(monkeypatch, capsys):
    msg = "Schwab refresh token expired or within 24h of expiry; re-run the OAuth flow."

    def boom(*a, **k):
        raise SchwabReauthRequiredError(msg)

    monkeypatch.setattr(schwab_bars.schwab.SchwabEquityVendor, "fetch", boom)
    err = run_fail(capsys, ["--ticker", "NVDA"], 2)
    assert err.strip() == msg


def test_empty_candles_exit_3(monkeypatch, capsys):
    patch_payload(monkeypatch, [])  # fetch raises NoMarketDataError
    err = run_fail(capsys, ["--ticker", "NVDA"], 3)
    assert "NVDA" in err


def test_no_bars_within_asof_exit_3(monkeypatch, capsys):
    patch_payload(monkeypatch, make_candles(5))  # all bars after the asof below
    run_fail(capsys, ["--ticker", "NVDA", "--asof", "2020-01-01"], 3)


def test_http_flake_exit_1(monkeypatch, capsys):
    def boom(path, params):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(schwab_bars.schwab, "_request", boom)
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
