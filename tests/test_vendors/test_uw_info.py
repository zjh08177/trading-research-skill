"""Offline tests for scripts/vendors/uw_info.py and the uw_fundamental distiller
(the beta-only P3 restore after the Schwab sunset)."""
import sys
import pathlib

import pytest

import _uw_common as uw
import uw_info

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
from distillers import uw_fundamental  # noqa: E402
from distillers._base import DistillCtx  # noqa: E402

INFO = {"symbol": "NVDA", "beta": "1.8739", "marketcap": "5019080000000",
        "sector": "Technology"}


def _ctx():
    return DistillCtx(ticker="NVDA", kind="equity", asof="2026-07-17", mode="live",
                      facts={}, spot=200.0, atr=5.0, max_rows=4, max_tokens=None, entry=None)


def test_cli_emits_raw_info(monkeypatch, capsys):
    monkeypatch.setattr(uw_info.uw, "get_json", lambda p, params=None: (200, {"data": INFO}))
    assert uw_info.main(["--ticker", "NVDA"]) == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["_uw_info"]["beta"] == "1.8739"


def test_cli_auth_and_nodata_exit_codes(monkeypatch):
    monkeypatch.setattr(uw_info.uw, "get_json", lambda p, params=None: (401, {}))
    with pytest.raises(SystemExit) as e:
        uw_info.fetch_info("NVDA")
    assert e.value.code == 2
    monkeypatch.setattr(uw_info.uw, "get_json", lambda p, params=None: (200, {"data": {}}))
    with pytest.raises(SystemExit) as e:
        uw_info.fetch_info("NVDA")
    assert e.value.code == 3


def test_distiller_emits_beta_notable_over_threshold():
    sigs = uw_fundamental.distill({"beta": 1.8739}, _ctx())
    assert len(sigs) == 1
    s = sigs[0]
    assert s["id"] == "P3.beta" and s["v"] == 1.8739
    assert s["src"] == "uw(info)" and s["notable"] is True


def test_distiller_low_beta_not_notable():
    sigs = uw_fundamental.distill({"beta": "0.85"}, _ctx())
    assert sigs[0]["id"] == "P3.beta" and sigs[0]["notable"] is False


def test_distiller_missing_beta_quiet():
    for raw in ({}, {"beta": None}, {"beta": "n/a"}):
        sigs = uw_fundamental.distill(raw, _ctx())
        assert sigs[0]["id"] == "P3.fundamental_quiet"
        assert sigs[0]["notable"] is False
