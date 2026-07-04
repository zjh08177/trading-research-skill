"""Offline tests for scripts/vendors/schwab_options.py (seam: upstream _request)."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import schwab_options as cli
from tradingagents.dataflows import schwab_options as upstream
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "vendors" / "schwab_options_chain.json"
QUOTE_TIME_MS = 1751500800000
# Hand-summed from the fixture (sentinel contracts included in volume/OI math).
CALL_VOL, PUT_VOL = 200 + 500 + 100 + 50, 400 + 100
CALL_OI, PUT_OI = 300 + 1000 + 2000 + 500, 1500 + 3000


@pytest.fixture()
def payload():
    return json.loads(FIXTURE.read_text())


def patch_request(monkeypatch, payload):
    monkeypatch.setattr(upstream, "_request", lambda path, params: copy.deepcopy(payload))


def patch_raising(monkeypatch, exc):
    def _raise(path, params):
        raise exc

    monkeypatch.setattr(upstream, "_request", _raise)


def run(capsys, argv=("--ticker", "NVDA")):
    rc = cli.main(list(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_happy_path(monkeypatch, capsys, payload):
    patch_request(monkeypatch, payload)
    rc, out, _ = run(capsys, ("--ticker", "NVDA", "--top-oi", "3"))
    assert rc == 0
    assert out.endswith("\n") and out.count("\n") == 1  # exactly one line
    facts = json.loads(out)
    # ATM IV excludes the -999 sentinel: clean ATM call at nearest expiry is strike 105.
    assert facts["P4.atm_iv_near"]["v"] == pytest.approx(0.45)
    term = facts["P4.iv_term"]["v"]
    assert [e for e, _ in term] == ["2026-07-17", "2026-08-21", "2026-09-18"]
    assert [iv for _, iv in term] == pytest.approx([0.45, 0.40, 0.38])
    assert facts["P4.put_call_volume_ratio"]["v"] == pytest.approx(PUT_VOL / CALL_VOL)
    assert facts["P4.put_call_oi_ratio"]["v"] == pytest.approx(PUT_OI / CALL_OI)
    noi = facts["P4.notable_oi"]["v"]
    assert [row["oi"] for row in noi] == [3000, 2000, 1500]  # sorted desc, capped at 3
    assert noi[0] == {"expiry": "2026-07-17", "strike": 90.0, "type": "PUT", "oi": 3000}
    assert facts["P4.is_delayed"]["v"] is True
    expected_asof = datetime.fromtimestamp(QUOTE_TIME_MS / 1000, tz=timezone.utc).isoformat()
    for f in facts.values():
        assert set(f) == {"v", "unit", "asof", "src"}
        assert f["src"] == "schwab"
        assert f["asof"] == expected_asof
        assert f["v"] is not None


def test_zero_call_volume_omits_volume_ratio(monkeypatch, capsys, payload):
    p = copy.deepcopy(payload)
    for strike_map in p["callExpDateMap"].values():
        for rows in strike_map.values():
            for c in rows:
                c["totalVolume"] = 0
    patch_request(monkeypatch, p)
    rc, out, _ = run(capsys)
    assert rc == 0
    facts = json.loads(out)
    assert "P4.put_call_volume_ratio" not in facts
    assert facts["P4.put_call_oi_ratio"]["v"] == pytest.approx(PUT_OI / CALL_OI)


def test_empty_chain_exits_3(monkeypatch, capsys):
    patch_request(monkeypatch, {"status": "FAILED"})
    rc, out, err = run(capsys)
    assert rc == 3
    assert out == ""
    assert err.strip()


def test_sparse_contract_missing_delta_exits_1(monkeypatch, capsys, payload):
    p = copy.deepcopy(payload)
    del p["callExpDateMap"]["2026-08-21:49"]["100.0"][0]["delta"]
    patch_request(monkeypatch, p)
    rc, out, err = run(capsys)
    assert rc == 1
    assert out == ""
    assert err.strip()


def test_rate_limit_exits_4(monkeypatch, capsys):
    patch_raising(monkeypatch, VendorRateLimitError("Schwab rate limit: HTTP 429."))
    rc, out, err = run(capsys)
    assert rc == 4
    assert out == ""
    assert "429" in err


def test_not_configured_exits_2_verbatim(monkeypatch, capsys):
    msg = "SCHWAB_ACCESS_TOKEN environment variable is not set."
    patch_raising(monkeypatch, VendorNotConfiguredError(msg))
    rc, out, err = run(capsys)
    assert rc == 2
    assert out == ""
    assert msg in err


def test_asof_falls_back_to_fetched_at(monkeypatch, capsys, payload):
    p = copy.deepcopy(payload)
    del p["quoteTime"]  # no underlying.quoteTime either -> chain.as_of is None
    patch_request(monkeypatch, p)
    before = datetime.now(timezone.utc)
    rc, out, _ = run(capsys)
    after = datetime.now(timezone.utc)
    assert rc == 0
    facts = json.loads(out)
    asof = datetime.fromisoformat(facts["P4.atm_iv_near"]["asof"])
    assert before <= asof <= after
