"""Offline tests for scripts/vendors/uw_quote.py — the UW-sourced live-quote CLI.

Monkeypatches the ``_uw_common.get_json`` seam with a canned ``stock-state``
payload. Pins the P1.last contract, the deliberate ``is_realtime=False`` (writer
boxes DELAYED until UW real-time is verified), and the current-day-only guard.
"""
import datetime

import pytest

import _uw_common as uw
import uw_quote

STATE = {
    "close": "334.83", "high": "335.2", "low": "332.56", "open": "333.24",
    "volume": 11605213, "total_volume": 62970384, "market_time": "postmarket",
    "tape_time": "2026-07-16T23:59:28Z", "prev_close": "333.26",
}


def patch(monkeypatch, body, status=200):
    monkeypatch.setattr(uw_quote.uw, "get_json", lambda path, params=None: (status, {"data": body}))


def test_p1_contract_and_tape_asof(monkeypatch):
    facts = uw_quote.build_facts(STATE)
    assert facts["P1.last"]["v"] == 334.83
    assert facts["P1.day_high"]["v"] == 335.2
    assert facts["P1.day_low"]["v"] == 332.56
    assert facts["P1.day_volume"]["v"] == 62970384.0
    assert all(v["src"] == "uw" for v in facts.values())
    assert all(v["asof"] == "2026-07-16T23:59:28Z" for v in facts.values())


def test_is_realtime_false_boxes_delayed(monkeypatch):
    # Deliberate: UW REST real-time entitlement unverified -> False -> DELAYED.
    facts = uw_quote.build_facts(STATE)
    assert facts["P1.is_realtime"]["v"] is False


def test_missing_close_exits_3(monkeypatch):
    with pytest.raises(SystemExit) as e:
        uw_quote.build_facts({"high": "1"})
    assert e.value.code == 3


def test_past_asof_rejected(monkeypatch):
    patch(monkeypatch, STATE)
    with pytest.raises(SystemExit) as e:
        uw_quote.main(["--ticker", "AAPL", "--asof", "2026-06-30"])
    assert e.value.code == 3


def test_bad_asof_rejected(monkeypatch):
    with pytest.raises(SystemExit) as e:
        uw_quote.main(["--ticker", "AAPL", "--asof", "notadate"])
    assert e.value.code == 2


def test_today_asof_accepted(monkeypatch, capsys):
    patch(monkeypatch, STATE)
    today = datetime.date.today().isoformat()
    rc = uw_quote.main(["--ticker", "AAPL", "--asof", today])
    assert rc == 0
    assert '"P1.last"' in capsys.readouterr().out


def test_auth_failure_exit_2(monkeypatch):
    monkeypatch.setattr(uw_quote.uw, "get_json", lambda p, params=None: (401, {"error": "x"}))
    with pytest.raises(SystemExit) as e:
        uw_quote.fetch_state("AAPL")
    assert e.value.code == 2


def test_empty_state_exit_3(monkeypatch):
    monkeypatch.setattr(uw_quote.uw, "get_json", lambda p, params=None: (200, {"data": {}}))
    with pytest.raises(SystemExit) as e:
        uw_quote.fetch_state("AAPL")
    assert e.value.code == 3
