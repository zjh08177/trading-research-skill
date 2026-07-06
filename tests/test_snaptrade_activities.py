"""Tests for snaptrade_activities.py — normalization + offset/limit pagination.

The module is self-contained (no SDK import at import time), so these run on the
bare test interpreter. Fixtures are recorded-shape SnapTrade activity rows (keys
mapped live 2026-07-06); no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vendors"))
import snaptrade_activities as act  # noqa: E402

# A real-shaped equity BUY (Robinhood) — nested symbol/currency objects.
BUY_ROW = {
    "trade_date": "2021-01-29T00:00:00Z", "settlement_date": "2021-02-01T00:00:00Z",
    "institution": "Robinhood", "type": "BUY", "units": 10, "price": 5.0,
    "amount": -50.0, "fee": 0.0, "option_symbol": None, "option_type": None,
    "currency": {"code": "USD", "id": "c1", "name": "US Dollar"},
    "symbol": {"raw_symbol": "AAPL", "symbol": "AAPL", "description": "Apple", "id": "s1"},
    "description": "Bought 10 AAPL"}
# A DIVIDEND with no instrument (symbol object absent).
DIV_ROW = {"trade_date": "2026-06-25T00:00:00Z", "settlement_date": None,
           "institution": "Schwab", "type": "DIVIDEND", "units": None, "price": None,
           "amount": 1.23, "currency": {"code": "USD"}, "symbol": None,
           "option_symbol": None, "description": "Cash dividend"}
# An option assignment — underlying comes from option_symbol.
OPT_ROW = {"trade_date": "2024-04-30T00:00:00Z", "settlement_date": "2024-05-02T00:00:00Z",
           "institution": "Robinhood", "type": "OPTIONASSIGNMENT", "units": 1, "price": 0.0,
           "amount": 0.0, "currency": {"code": "USD"}, "symbol": None,
           "option_symbol": {"ticker": "TSLA", "raw_symbol": "TSLA240517C00200000"},
           "description": "Assigned"}


def test_normalize_maps_pinned_schema():
    r = act.normalize(BUY_ROW, "acct-1", "Robinhood")
    assert r == {"trade_date": "2021-01-29T00:00:00Z",
                 "settlement_date": "2021-02-01T00:00:00Z", "account_id": "acct-1",
                 "broker": "Robinhood", "symbol": "AAPL", "type": "BUY", "units": 10,
                 "price": 5.0, "amount": -50.0, "currency": "USD",
                 "description": "Bought 10 AAPL"}


def test_normalize_dividend_has_no_symbol():
    r = act.normalize(DIV_ROW, "acct-2", None)
    assert r["symbol"] is None and r["type"] == "DIVIDEND" and r["currency"] == "USD"
    assert r["broker"] == "Schwab"        # falls back to the row's institution


def test_sym_prefers_equity_then_option():
    assert act._sym(BUY_ROW) == "AAPL"
    assert act._sym(OPT_ROW) == "TSLA"    # option underlying from option_symbol
    assert act._sym(DIV_ROW) is None


class _Resp:
    def __init__(self, body):
        self.body = body


class _AI:
    def __init__(self, pages):
        self.pages, self.offsets = pages, []

    def get_account_activities(self, **kw):
        self.offsets.append(kw["offset"])
        return _Resp(self.pages[kw["offset"]])


class _Client:
    def __init__(self, pages):
        self.account_information = _AI(pages)


def test_fetch_account_walks_offset_limit_loop():
    # 1500 activities → page(0)=1000, page(1000)=500 (short page ends the loop)
    page0 = {"data": [dict(BUY_ROW) for _ in range(act.LIMIT)],
             "pagination": {"total": 1500, "offset": 0, "limit": act.LIMIT}}
    page1 = {"data": [dict(DIV_ROW) for _ in range(500)],
             "pagination": {"total": 1500, "offset": 1000, "limit": act.LIMIT}}
    c = _Client({0: page0, 1000: page1})
    rows = act.fetch_account(c, "u", "s", {"id": "acct-1", "institution_name": "RH"},
                             act._parse_date("2000-01-01", "--start"),
                             act._parse_date("2026-07-06", "--end"))
    assert len(rows) == 1500 and c.account_information.offsets == [0, 1000]
    assert rows[0]["symbol"] == "AAPL" and rows[-1]["type"] == "DIVIDEND"
    assert rows[0]["broker"] == "RH"      # account institution_name wins over row


def test_fetch_account_single_short_page_stops():
    page0 = {"data": [dict(BUY_ROW) for _ in range(3)], "pagination": {"total": 3}}
    c = _Client({0: page0})
    rows = act.fetch_account(c, "u", "s", {"id": "a"}, act._parse_date("2000-01-01", "--start"),
                             act._parse_date("2026-07-06", "--end"))
    assert len(rows) == 3 and c.account_information.offsets == [0]   # no second call


def test_scrub_redacts_user_secret():
    leaked = 'error {"userSecret": "abcd-secret-1234", "code": 500}'
    out = act._scrub(leaked)
    assert "abcd-secret-1234" not in out and "userSecret" in out and "***" in out
