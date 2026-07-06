"""Offline tests for the SnapTrade vendor CLIs (no network).

Split-by-design: ``build_position`` / ``build_holdings`` are pure and tested with
canned SnapTrade payloads; CLI I/O is tested by monkeypatching ``fetch``. The
read-only invariant is asserted against the account + holdings CLI source (the
register/login POSTs live only in snaptrade_setup.py, outside the pipeline).
"""
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

import pytest

import _snaptrade_common as sc
import snaptrade_account as sa
import snaptrade_holdings as sh

TODAY = datetime.now().date().isoformat()
ACCT_DIR = str(Path(__file__).resolve().parents[2] / "scripts" / "vendors")
SCRIPT = ACCT_DIR + "/snaptrade_account.py"


def acct(aid, inst, total):
    return {"id": aid, "institution": inst, "total_value": total}


def pos(sym, units, price, cost_basis=None, kind="stock"):
    ins = {"raw_symbol": sym, "symbol": sym, "kind": kind}
    p = {"instrument": ins, "units": str(units), "price": str(price)}
    if cost_basis is not None:
        p["cost_basis"] = str(cost_basis)
    return p


# ---- build_position: aggregation ----

def test_single_account_held():
    accts = [acct("A", "Robinhood", 40000.0)]
    posmap = {"A": [pos("NVDA", 100, 160.0, 100.0)]}
    a = sa.build_position("NVDA", accts, posmap)
    assert a["qty"] == pytest.approx(100.0)
    assert a["market_value"] == pytest.approx(16000.0)
    assert a["avg_price"] == pytest.approx(100.0)
    assert a["unrealized_pl"] == pytest.approx(6000.0)
    assert a["unrealized_pl_pct"] == pytest.approx(60.0)
    assert a["pct_of_book"] == pytest.approx(40.0)   # 16000/40000
    assert a["n_accounts"] == 1
    assert a["brokers"] == "Robinhood"
    assert a["have_cost"] is True


def test_multi_broker_aggregate():
    accts = [acct("A", "Robinhood", 20000.0), acct("B", "Schwab", 20000.0),
             acct("C", "Schwab", 10000.0)]
    posmap = {
        "A": [pos("NVDA", 100, 160.0, 100.0)],
        "B": [pos("NVDA", 100, 160.0, 140.0)],
        "C": [pos("AAPL", 5, 300.0, 100.0)],  # different symbol, ignored
    }
    a = sa.build_position("NVDA", accts, posmap)
    assert a["qty"] == pytest.approx(200.0)
    assert a["market_value"] == pytest.approx(32000.0)
    assert a["avg_price"] == pytest.approx(120.0)      # (100*100+100*140)/200
    assert a["n_accounts"] == 2
    assert a["brokers"] == "Robinhood, Schwab"          # distinct, first-seen
    assert a["pct_of_book"] == pytest.approx(64.0)      # 32000/50000


def test_missing_cost_basis_omits_pl():
    accts = [acct("A", "Fidelity", 10000.0)]
    posmap = {"A": [pos("TG3Y", 100, 17.0, cost_basis=None, kind="other")]}
    a = sa.build_position("TG3Y", accts, posmap)
    assert a["qty"] == pytest.approx(100.0)
    assert a["have_cost"] is False
    assert "avg_price" not in a and "unrealized_pl" not in a
    facts = sa.build_facts(a, "t")
    assert "H1.avg_price" not in facts and "H1.unrealized_pl" not in facts
    assert facts["H1.qty"]["v"] == pytest.approx(100.0)


def test_partial_missing_cost_basis_omits_pl():
    # one lot has cost, the other does not -> whole aggregate loses cost facts
    accts = [acct("A", "Fidelity", 100000.0), acct("B", "Robinhood", 100000.0)]
    posmap = {"A": [pos("NVDA", 10, 160.0, cost_basis=None)],
              "B": [pos("NVDA", 10, 160.0, 100.0)]}
    a = sa.build_position("NVDA", accts, posmap)
    assert a["qty"] == pytest.approx(20.0)
    assert a["have_cost"] is False


def test_long_only_excludes_short_and_option():
    accts = [acct("A", "Robinhood", 50000.0)]
    posmap = {"A": [
        pos("NVDA", -100, 160.0, 100.0),                  # short leg
        pos("NVDA 260101C00100000", 1, 5.0, 1.0, kind="option"),  # option
    ]}
    assert sa.build_position("NVDA", accts, posmap) is None


def test_flat_returns_none():
    accts = [acct("A", "Robinhood", 50000.0)]
    posmap = {"A": [pos("AAPL", 10, 300.0, 100.0)]}
    assert sa.build_position("NVDA", accts, posmap) is None


def test_zero_book_pct_zero():
    accts = [acct("A", "Robinhood", 0.0)]
    posmap = {"A": [pos("NVDA", 10, 160.0, 100.0)]}
    assert sa.build_position("NVDA", accts, posmap)["pct_of_book"] == 0.0


def test_pct_of_book_null_total_value_no_inflation():
    # account A reports no total_value but holds the ticker; it must still count
    # toward the book denominator (via its own position MV), never >100%.
    accts = [acct("A", "Robinhood", None), acct("B", "Schwab", 10000.0)]
    posmap = {"A": [pos("NVDA", 100, 160.0, 100.0)], "B": []}
    a = sa.build_position("NVDA", accts, posmap)
    assert a["market_value"] == pytest.approx(16000.0)
    # book = 16000 (A fallback) + 10000 (B) = 26000
    assert a["pct_of_book"] == pytest.approx(100.0 * 16000 / 26000)
    assert a["pct_of_book"] < 100.0


def test_lowercase_ticker_matches():
    accts = [acct("A", "Robinhood", 1000.0)]
    posmap = {"A": [pos("NVDA", 10, 10.0, 5.0)]}
    assert sa.build_position("nvda", accts, posmap)["qty"] == pytest.approx(10.0)


def test_string_numbers_parsed():
    # SnapTrade returns units/price/cost_basis as strings
    accts = [acct("A", "Robinhood", 40000.0)]
    posmap = {"A": [{"instrument": {"raw_symbol": "AAOI", "kind": "stock"},
                     "units": "170", "price": "120.94", "cost_basis": "165.746"}]}
    a = sa.build_position("AAOI", accts, posmap)
    assert a["qty"] == pytest.approx(170.0)
    assert a["avg_price"] == pytest.approx(165.746)
    assert a["unrealized_pl_pct"] == pytest.approx(-27.03, abs=0.02)


# ---- build_facts: shape ----

def test_build_facts_shape_and_src():
    a = sa.build_position("NVDA", [acct("A", "Robinhood", 40000.0)],
                          {"A": [pos("NVDA", 100, 160.0, 100.0)]})
    facts = sa.build_facts(a, "2026-07-04T00:00:00Z")
    for v in facts.values():
        assert set(v) == {"v", "unit", "asof", "src"} and v["src"] == "snaptrade"
    assert facts["H1.held"]["v"] is True
    assert facts["H1.qty"]["unit"] == "shares"
    assert facts["H1.brokers"]["v"] == "Robinhood"


def test_build_facts_flat():
    facts = sa.build_facts(None, "t")
    assert list(facts) == ["H1.held"] and facts["H1.held"]["v"] is False


# ---- CLI: guards + happy path (fetch monkeypatched) ----

def cli_ok(capsys, argv):
    assert sa.main(argv) == 0
    out, _ = capsys.readouterr()
    return json.loads(out)


def cli_fail(capsys, argv, code):
    with pytest.raises(SystemExit) as e:
        sa.main(argv)
    assert e.value.code == code
    out, err = capsys.readouterr()
    assert out == ""
    return err


def test_cli_happy(monkeypatch, capsys):
    monkeypatch.setattr(sa, "fetch", lambda t: (
        [acct("A", "Robinhood", 40000.0)], {"A": [pos("NVDA", 100, 160.0, 100.0)]}, []))
    f = cli_ok(capsys, ["--ticker", "NVDA"])
    assert f["H1.held"]["v"] is True and f["H1.qty"]["v"] == pytest.approx(100.0)
    assert f["H1.brokers"]["v"] == "Robinhood"
    assert "H1.accounts_skipped" not in f


def test_cli_flat(monkeypatch, capsys):
    monkeypatch.setattr(sa, "fetch", lambda t: (
        [acct("A", "Robinhood", 40000.0)], {"A": []}, []))
    f = cli_ok(capsys, ["--ticker", "NVDA"])
    assert list(f) == ["H1.held"] and f["H1.held"]["v"] is False


def test_cli_surfaces_skipped_accounts(monkeypatch, capsys):
    monkeypatch.setattr(sa, "fetch", lambda t: (
        [acct("A", "Robinhood", 40000.0)], {"A": [pos("NVDA", 100, 160.0, 100.0)]},
        ["B"]))
    f = cli_ok(capsys, ["--ticker", "NVDA"])
    assert f["H1.accounts_skipped"]["v"] == 1


def test_cli_past_asof_exit_3(capsys):
    cli_fail(capsys, ["--ticker", "NVDA", "--asof", "2020-01-01"], 3)


def test_cli_future_asof_exit_3(capsys):
    cli_fail(capsys, ["--ticker", "NVDA", "--asof", "2099-01-01"], 3)


def test_cli_malformed_asof_exit_2(capsys):
    assert "invalid --asof" in cli_fail(capsys, ["--ticker", "X", "--asof", "nope"], 2)


def test_cli_asof_today_ok(monkeypatch, capsys):
    monkeypatch.setattr(sa, "fetch", lambda t: (
        [acct("A", "Robinhood", 100.0)], {"A": [pos("NVDA", 1, 2.0, 1.0)]}, []))
    assert cli_ok(capsys, ["--ticker", "NVDA", "--asof", TODAY])["H1.held"]["v"] is True


def test_cli_no_creds_exit_2(tmp_path):
    envfile = tmp_path / "snaptrade.env"
    envfile.write_text("SNAPTRADE_CLIENT_ID=PERS-x\nSNAPTRADE_CONSUMER_KEY=y\n")
    env = dict(os.environ)
    env["SNAPTRADE_ENV"] = str(envfile)
    for k in ("SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, SCRIPT, "--ticker", "AAOI"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 2 and r.stdout == "" and r.stderr.strip()


# ---- fetch: empty-accounts guard + per-account resilience (blocking finding) ----

class _Resp:
    def __init__(self, body):
        self.body = body


def _fake_client(accts_body, positions_fn):
    class AI:
        def list_user_accounts(self, **k):
            return _Resp(accts_body)

        def get_all_account_positions(self, **k):
            return positions_fn(k["account_id"])
    return type("C", (), {"account_information": AI()})()


def test_fetch_empty_accounts_exit_2(monkeypatch):
    # zero linked accounts is data-absence, not a flat position -> exit 2 so the
    # Schwab fallback runs (never a confident held=false).
    monkeypatch.setattr(sa, "user_creds", lambda: ("u", "s"))
    monkeypatch.setattr(sa, "client", lambda: _fake_client([], lambda aid: _Resp([])))
    with pytest.raises(SystemExit) as e:
        sa.fetch("AAPL")
    assert e.value.code == 2


def test_fetch_skips_transient_account_error(monkeypatch):
    accts = [{"id": "A", "institution_name": "RH", "balance": {"total": {"amount": 100}}},
             {"id": "B", "institution_name": "SCH", "balance": {"total": {"amount": 100}}}]

    class Boom(Exception):
        status = 503

    def posfn(aid):
        if aid == "B":
            raise Boom("down")
        return _Resp([pos("NVDA", 10, 100.0, 50.0)])
    monkeypatch.setattr(sa, "user_creds", lambda: ("u", "s"))
    monkeypatch.setattr(sa, "client", lambda: _fake_client(accts, posfn))
    accounts, posmap, failed = sa.fetch("NVDA")
    assert failed == ["B"] and posmap["B"] == [] and len(posmap["A"]) == 1


def test_fetch_auth_error_hard_fails(monkeypatch):
    accts = [{"id": "A", "institution_name": "RH", "balance": {"total": {"amount": 100}}}]

    class Auth(Exception):
        status = 401

    def posfn(aid):
        raise Auth("bad token")
    monkeypatch.setattr(sa, "user_creds", lambda: ("u", "s"))
    monkeypatch.setattr(sa, "client", lambda: _fake_client(accts, posfn))
    with pytest.raises(SystemExit) as e:
        sa.fetch("NVDA")
    assert e.value.code == 2


def test_missing_price_counts_qty_omits_value():
    # a matched lot with no live price must NOT drop the shares from qty; value
    # facts are omitted rather than understated.
    accts = [acct("A", "Robinhood", 40000.0)]
    posmap = {"A": [{"instrument": {"raw_symbol": "NVDA", "kind": "stock"},
                     "units": "10", "cost_basis": "100"}]}  # no price
    a = sa.build_position("NVDA", accts, posmap)
    assert a["qty"] == pytest.approx(10.0) and a["have_price"] is False
    facts = sa.build_facts(a, "t")
    assert facts["H1.held"]["v"] is True and facts["H1.qty"]["v"] == pytest.approx(10.0)
    assert "H1.market_value" not in facts and "H1.pct_of_book" not in facts
    assert "H1.avg_price" not in facts


# ---- build_holdings ----

def test_build_holdings_aggregates_and_sorts():
    accts = [acct("A", "Robinhood", 50000.0), acct("B", "Schwab", 50000.0)]
    posmap = {"A": [pos("NVDA", 10, 200.0, 100.0), pos("AAPL", 5, 100.0, 50.0)],
              "B": [pos("NVDA", 10, 200.0, 150.0)]}
    res = sh.build_holdings(accts, posmap)
    assert res["total_book"] == pytest.approx(100000.0)
    rows = {r["symbol"]: r for r in res["holdings"]}
    assert rows["NVDA"]["qty"] == pytest.approx(20.0)
    assert rows["NVDA"]["brokers"] == "Robinhood, Schwab"
    assert res["holdings"][0]["symbol"] == "NVDA"   # sorted by MV desc (4000 > 500)


def test_build_holdings_price_is_vwap():
    # a symbol split across accounts at differing prices: row price must be the
    # VWAP so qty*price == market_value (self-consistent row).
    accts = [acct("A", "Robinhood", 50000.0), acct("B", "Schwab", 50000.0)]
    posmap = {"A": [pos("NVDA", 10, 200.0, 100.0)],
              "B": [pos("NVDA", 10, 201.0, 100.0)]}
    row = {r["symbol"]: r for r in sh.build_holdings(accts, posmap)["holdings"]}["NVDA"]
    assert row["market_value"] == pytest.approx(4010.0)
    assert row["price"] == pytest.approx(200.5)
    assert row["qty"] * row["price"] == pytest.approx(row["market_value"])


def test_build_holdings_missing_cost_null():
    accts = [acct("A", "Fidelity", 10000.0)]
    posmap = {"A": [pos("TG3Y", 100, 17.0, cost_basis=None, kind="other")]}
    row = sh.build_holdings(accts, posmap)["holdings"][0]
    assert row["avg_cost"] is None and row["unrealized_pl_pct"] is None


def test_build_holdings_excludes_options():
    accts = [acct("A", "Robinhood", 10000.0)]
    posmap = {"A": [pos("NVDA 260101C00100000", 1, 5.0, 1.0, kind="option")]}
    assert sh.build_holdings(accts, posmap)["holdings"] == []


# ---- secrets: redaction + creds file perms ----

def test_scrub_redacts_secret():
    s = 'ApiException 400 for userSecret=abc-123-def, consumerKey=k9xyz, signature=sig-000'
    out = sc._scrub(s)
    assert "abc-123-def" not in out and "k9xyz" not in out and "sig-000" not in out
    assert "***" in out


def test_die_from_exc_scrubs_secret(capsys):
    class E(Exception):
        status = 400
    with pytest.raises(SystemExit):
        sc.die_from_exc(E("failed for userSecret=super-secret-value"))
    err = capsys.readouterr().err
    assert "super-secret-value" not in err and "***" in err


def test_save_user_creds_mode_0600(tmp_path, monkeypatch):
    envf = tmp_path / "snaptrade.env"
    monkeypatch.setattr(sc, "CREDS_PATH", envf)
    sc.save_user_creds("u@x.com", "secret-123")
    assert oct(os.stat(envf).st_mode & 0o777) == "0o600"
    txt = envf.read_text()
    assert "SNAPTRADE_USER_ID=u@x.com" in txt
    assert "SNAPTRADE_USER_SECRET=secret-123" in txt


# ---- read-only invariant ----

def test_read_only_no_mutation_paths():
    src = (inspect.getsource(sa) + inspect.getsource(sh)).lower()
    for bad in ("/orders", "placeorder", "place_order", ".post(", ".put(",
                ".delete(", "cancel_order", ".trading."):
        assert bad not in src, "mutation reference %r in read CLI" % bad
    # the only account_information methods referenced are reads
    assert "list_user_accounts" in src and "get_all_account_positions" in src
