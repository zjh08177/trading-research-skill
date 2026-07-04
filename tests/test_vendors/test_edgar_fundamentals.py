"""Offline tests for scripts/vendors/edgar_fundamentals.py (design test_plan cases 1-11)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import edgar_fundamentals as ef
from tradingagents.dataflows import edgar
from tradingagents.dataflows.edgar import EdgarNotConfiguredError, EdgarRateLimitError
from tradingagents.dataflows.errors import NoMarketDataError

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "vendors" / "edgar_companyfacts.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())
ASOF = "2026-07-01"
REV_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"
# hand-sums from the fixture (see fixture: 8 discrete quarters, last one restated to 145)
REV_TTM = 110 + 120 + 130 + 145  # == 505
REV_PREV_TTM = 80 + 85 + 90 + 100  # == 355


@pytest.fixture
def facts():
    return copy.deepcopy(FIXTURE)


@pytest.fixture
def ua(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test test@example.com")


def patch_getter(monkeypatch, payload=None, exc=None):
    def fake(ticker, curr_date=None):
        if exc is not None:
            raise exc
        return payload
    monkeypatch.setattr(edgar, "get_fundamentals", fake)


def main_exit(argv=("--ticker", "TEST", "--asof", ASOF)):
    with pytest.raises(SystemExit) as si:
        ef.main(list(argv))
    return si.value.code


# --- case 1: TTM revenue sums exactly the 4 discrete quarters (YTD rows excluded) ---
def test_ttm_revenue_excludes_ytd_rows(facts):
    out = ef.build_facts(facts, ASOF)
    f = out["P3.revenue_ttm"]
    assert f["v"] == REV_TTM
    assert f["unit"] == "USD" and f["asof"] == "2026-03-31" and f["src"] == "sec-edgar"
    assert out["P3.revenue_yoy"]["v"] == pytest.approx((REV_TTM / REV_PREV_TTM - 1) * 100)
    assert out["P3.eps_diluted_ttm"]["v"] == pytest.approx(1.0 + 1.1 + 1.2 + 1.3)
    assert out["P3.eps_yoy"]["v"] == pytest.approx((4.6 / 2.6 - 1) * 100)
    assert out["P3.gross_margin_ttm"]["v"] == pytest.approx((505 - 243) / 505 * 100)
    assert out["P3.operating_margin_ttm"]["v"] == pytest.approx(126 / 505 * 100)
    assert out["P3.net_margin_ttm"]["v"] == pytest.approx(101 / 505 * 100)
    assert out["P3.total_debt"]["v"] == 1100  # LongTermDebtNoncurrent + DebtCurrent
    assert out["P3.cash_and_equivalents"]["v"] == 300
    assert out["P3.net_debt"]["v"] == 800
    assert out["P3.shares_outstanding"]["v"] == 5000
    assert out["P3.shares_outstanding"]["unit"] == "shares"


# --- case 2: restatement dedupe keeps the max-'filed' value ---
def test_restatement_dedupe_keeps_latest_filed(facts):
    rows = facts["facts"]["us-gaap"][REV_TAG]["units"]["USD"]
    without = [r for r in rows if r["filed"] != "2026-06-01"]
    facts["facts"]["us-gaap"][REV_TAG]["units"]["USD"] = without
    assert ef.build_facts(facts, ASOF)["P3.revenue_ttm"]["v"] == 500  # original 140
    out = ef.build_facts(FIXTURE, ASOF)
    assert out["P3.revenue_ttm"]["v"] == 505  # restated 145 wins


# --- case 3: FCF is FY-based (10-K OCF minus capex), not quarterly YTD ---
def test_fcf_fy_based(facts):
    f = ef.build_facts(facts, ASOF)["P3.fcf_ttm"]
    assert f["v"] == 200 - 50
    assert f["asof"] == "2025-06-30"  # FY period end, not a quarter end


# --- case 4: revenue tag fallback to 'Revenues' ---
def test_revenue_tag_fallback(facts):
    facts["facts"]["us-gaap"]["Revenues"] = facts["facts"]["us-gaap"].pop(REV_TAG)
    out = ef.build_facts(facts, ASOF)
    assert out["P3.revenue_ttm"]["v"] == REV_TTM


# --- case 5: no debt tags -> total_debt AND net_debt omitted, cash still emitted ---
def test_no_debt_tags_omitted(facts):
    del facts["facts"]["us-gaap"]["LongTermDebtNoncurrent"]
    del facts["facts"]["us-gaap"]["DebtCurrent"]
    out = ef.build_facts(facts, ASOF)
    assert "P3.total_debt" not in out and "P3.net_debt" not in out
    assert out["P3.cash_and_equivalents"]["v"] == 300


# --- case 6: gross_margin omitted when CostOfRevenue absent (no GrossProfit either) ---
def test_gross_margin_omitted_without_cost_of_revenue(facts):
    del facts["facts"]["us-gaap"]["CostOfRevenue"]
    assert "P3.gross_margin_ttm" not in ef.build_facts(facts, ASOF)


# --- case 7: latest filing dates = max row-level 'filed' per form ---
def test_latest_filed_dates(facts):
    out = ef.build_facts(facts, ASOF)
    assert out["P3.latest_10k_filed"]["v"] == "2025-08-15"
    assert out["P3.latest_10k_filed"]["unit"] == "date"
    assert out["P3.latest_10q_filed"]["v"] == "2026-06-01"


# --- case 8: not configured -> exit 2, stdout empty ---
def test_not_configured_exit_2(monkeypatch, capsys):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    patch_getter(monkeypatch, exc=EdgarNotConfiguredError("SEC_EDGAR_USER_AGENT is not set"))
    assert main_exit() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SEC_EDGAR_USER_AGENT" in captured.err


# --- case 9: rate limit -> exit 4 ---
def test_rate_limit_exit_4(monkeypatch, capsys, ua):
    patch_getter(monkeypatch, exc=EdgarRateLimitError("SEC EDGAR throttled the request: HTTP 429."))
    assert main_exit() == 4
    assert capsys.readouterr().out == ""


# --- case 10: malformed JSON string from the getter -> exit 1, stdout empty ---
def test_malformed_json_exit_1(monkeypatch, capsys, ua):
    patch_getter(monkeypatch, payload="this is not json {")
    assert main_exit() == 1
    assert capsys.readouterr().out == ""


# --- case 11: every fact's asof == its underlying period 'end' ---
def test_asof_per_fact_is_period_end(facts):
    out = ef.build_facts(facts, ASOF)
    expected = {
        "P3.revenue_ttm": "2026-03-31",
        "P3.revenue_yoy": "2026-03-31",
        "P3.eps_diluted_ttm": "2026-03-31",
        "P3.eps_yoy": "2026-03-31",
        "P3.gross_margin_ttm": "2026-03-31",
        "P3.operating_margin_ttm": "2026-03-31",
        "P3.net_margin_ttm": "2026-03-31",
        "P3.fcf_ttm": "2025-06-30",
        "P3.total_debt": "2026-03-31",
        "P3.cash_and_equivalents": "2026-03-31",
        "P3.net_debt": "2026-03-31",
        "P3.shares_outstanding": "2026-04-15",
        "P3.latest_10k_filed": "2025-06-30",
        "P3.latest_10q_filed": "2026-03-31",
    }
    assert {k: v["asof"] for k, v in out.items()} == expected


# --- asof windowing: quarters after --asof are excluded ---
def test_asof_windowing(facts):
    out = ef.build_facts(facts, "2026-01-15")
    assert out["P3.revenue_ttm"]["v"] == 100 + 110 + 120 + 130
    assert out["P3.revenue_ttm"]["asof"] == "2025-12-31"


# --- no-data paths -> exit 3 ---
def test_no_market_data_exit_3(monkeypatch, capsys, ua):
    patch_getter(monkeypatch, exc=NoMarketDataError("TEST", detail="not in SEC map"))
    assert main_exit() == 3
    assert capsys.readouterr().out == ""


def test_empty_payload_exit_3(monkeypatch, capsys, ua):
    patch_getter(monkeypatch, payload=json.dumps({"facts": {}}))
    assert main_exit() == 3
    assert capsys.readouterr().out == ""


# --- cross-cutting: success stdout is one line of JSON, every value well-formed ---
def test_main_success_stdout_contract(monkeypatch, capsys, ua):
    patch_getter(monkeypatch, payload=json.dumps(FIXTURE))
    assert ef.main(["--ticker", "TEST", "--asof", ASOF]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")
    parsed = json.loads(out)
    assert parsed
    for key, val in parsed.items():
        assert key.startswith("P3.")
        assert set(val) == {"v", "unit", "asof", "src"}
        assert val["v"] is not None
        assert val["src"] == "sec-edgar"


# --- subprocess smoke: seam unpatchable, UA blanked -> exit 2, stdout empty, offline ---
def test_subprocess_smoke_no_stdout_on_error():
    script = Path(__file__).parents[2] / "scripts" / "vendors" / "edgar_fundamentals.py"
    env = {**os.environ, "SEC_EDGAR_USER_AGENT": ""}  # blocks .env value (override=False)
    proc = subprocess.run(
        [sys.executable, str(script), "--ticker", "TEST"],
        capture_output=True, env=env, timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert b"SEC_EDGAR_USER_AGENT" in proc.stderr
