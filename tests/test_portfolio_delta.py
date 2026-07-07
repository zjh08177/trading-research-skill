"""Tests for portfolio_delta.py — delta classification + the §3.1 adherence matrix.

The matrix is the semantic core; there is one test per cell. build_report is
called directly with in-memory snapshots/ledger/sidecars (no I/O); main() is
exercised for the exit-code contract and md+json emit.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import portfolio_delta as pd  # noqa: E402


def H(sym, qty, price=10.0, kind="equity", avg_cost=None, mv=None):
    return {"symbol": sym, "kind": kind, "qty": qty, "price": price,
            "market_value": qty * price if mv is None else mv, "avg_cost": avg_cost}


def vend(holdings, skipped=0):
    return {"accounts_skipped": skipped, "n_accounts": 2, "total_book": 1000.0,
            "holdings": holdings}


def env(holdings, skipped=0):
    return {"kind": "holdings-snapshot", "schema": 1, "asof_date": "x",
            "fetched_at": "x", "vendor": vend(holdings, skipped)}


def trig(sym, action, date="2026-07-06"):
    return {"ticker": sym, "dir": "▲", "fired": True, "level": 1.0,
            "action": action, "basis": "SMA20", "date": date}


def rate(sym, rating, as_of=None, date_utc=None, no_call=False):
    r = {"ticker": sym, "mode_rating": rating, "no_call": no_call}
    if as_of:
        r["as_of"] = as_of
    if date_utc:
        r["date_utc"] = date_utc
    return r


def report(old_h, new_h, ledger=None, sidecars=None, old_skip=0, new_skip=0):
    return pd.build_report("2026-07-05", "2026-07-06",
                           env(old_h, old_skip), env(new_h, new_skip),
                           ledger or [], sidecars or [])


def by_sym(rep):
    return {c["symbol"]: c for c in rep["changes"]}


# ---- delta classification ----

def test_new_exited_added_trimmed_incl_fractional_crypto():
    rep = report(
        [H("KEEP", 10), H("TRIM", 20), H("GONE", 5), H("BTC", 0.5, 60000.0, "crypto")],
        [H("KEEP", 10), H("TRIM", 12), H("NEW", 3), H("BTC", 0.9, 60000.0, "crypto")])
    m = by_sym(rep)
    assert "KEEP" not in m                                   # unchanged qty omitted
    assert m["NEW"]["change"] == "New" and m["NEW"]["action_dir"] == 1
    assert m["GONE"]["change"] == "Exited" and m["GONE"]["action_dir"] == -1
    assert m["TRIM"]["change"] == "Trimmed" and m["TRIM"]["action_dir"] == -1
    assert m["BTC"]["change"] == "Added" and round(m["BTC"]["dq"], 4) == 0.4   # fractional crypto add


def test_sub_epsilon_qty_flip_makes_no_row():
    rep = report([H("AAA", 100.0)], [H("AAA", 100.0 + 5e-5)])   # < 1e-6·100 = 1e-4
    assert rep["changes"] == []


def test_value_delta_at_newer_price():
    rep = report([H("AAA", 10, 10.0)], [H("AAA", 15, 12.0)])    # +5 @ newer price 12
    assert by_sym(rep)["AAA"]["value_delta"] == 60.0


def test_basis_restated_on_unchanged_qty():
    rep = report([H("AAA", 10, avg_cost=8.0)], [H("AAA", 10, avg_cost=9.5)])
    c = by_sym(rep)["AAA"]
    assert c["change"] == "basis-restated" and c["verdict"] is None
    assert c["avg_cost_old"] == 8.0 and c["avg_cost_new"] == 9.5


def test_cash_mmf_and_pinned_junk_excluded():
    rep = report(
        [H("AAA", 10)],
        [H("AAA", 10), H("SWVXX", 999, kind="mutualfund"), H("O92E", 1), H("PS", 2)])
    assert rep["changes"] == []                              # every added row is excluded junk


# ---- §3.1 matrix: trigger axis (fired trigger outranks rating) ----

def test_trigger_axis_followed():
    rep = report([H("AAA", 10)], [H("AAA", 15)], sidecars=[trig("AAA", "Add")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["axis"] == "trigger"


def test_trigger_axis_against():
    rep = report([H("AAA", 20)], [H("AAA", 10)], sidecars=[trig("AAA", "Add")])
    c = by_sym(rep)["AAA"]                                   # trigger says Add, owner trimmed
    assert c["verdict"] == "against" and c["axis"] == "trigger"


def test_trigger_outranks_rating():
    # trigger −1 (Trim), rating +1 (Buy), owner trimmed → followed on the TRIGGER axis
    rep = report([H("AAA", 20)], [H("AAA", 10)],
                 ledger=[rate("AAA", "Buy", as_of="2026-07-01")],
                 sidecars=[trig("AAA", "Trim")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["axis"] == "trigger"


def test_conflicting_triggers_mixed():
    rep = report([H("AAA", 20)], [H("AAA", 10)],
                 sidecars=[trig("AAA", "Add"), trig("AAA", "Trim")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "mixed" and len(c["triggers"]) == 2


# ---- §3.1 matrix: rating axis (no directional trigger) ----

def test_rating_axis_followed():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Buy", as_of="2026-07-01")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["axis"] == "rating"


def test_rating_axis_against():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Sell", as_of="2026-07-01")])
    c = by_sym(rep)["AAA"]                                   # rating Sell, owner added
    assert c["verdict"] == "against" and c["axis"] == "rating"


def test_informational_trigger_falls_to_rating_axis():
    # a re-rate trigger has no direction → grade on the rating axis
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Buy", as_of="2026-07-01")],
                 sidecars=[trig("AAA", "Stop trimming / re-rate")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["axis"] == "rating"


def test_no_call_when_neither_axis_has_direction():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Hold", as_of="2026-07-01")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "against Hold discipline" and c["axis"] == "discipline"


def test_review_only_trigger_change_on_hold_is_discipline_breach():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Hold", as_of="2026-07-01")],
                 sidecars=[{**trig("AAA", "Add"), "state": "crossed_unconfirmed"}])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "review-only action changed size"
    assert c["axis"] == "discipline"


def test_amd_msft_replay_changes_are_hold_discipline_breaches():
    rep = report([H("AMD", 60, 517.82), H("MSFT", 50, 390.49)],
                 [H("AMD", 70, 521.15), H("MSFT", 35, 392.06)],
                 ledger=[rate("AMD", "Hold", as_of="2026-07-05"),
                         rate("MSFT", "Hold", as_of="2026-07-05")],
                 sidecars=[{**trig("AMD", "Add"), "state": "crossed_unconfirmed"},
                           {**trig("MSFT", "Trim"), "state": "near"}])
    m = by_sym(rep)
    assert m["AMD"]["verdict"] == "review-only action changed size"
    assert m["MSFT"]["verdict"] == "against Hold discipline"


def test_no_call_row_carries_no_direction():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Buy", as_of="2026-07-01", no_call=True)])
    assert by_sym(rep)["AAA"]["verdict"] == "no-call"


# ---- rating join window ----

def test_same_day_call_in_window_counts_as_followed_and_is_labeled():
    # rating updated 07-06 (in (05,06]) → precedes-or-ties the action at day grain
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Buy", as_of="2026-07-06")])
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["rating_in_window"] is True
    assert "call updated in window" in pd._verdict_cell(c)


def test_window_call_beats_older_baseline():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Sell", as_of="2026-07-01"),      # stale baseline
                         rate("AAA", "Buy", as_of="2026-07-06")])      # in-window update
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["rating_asof"] == "2026-07-06"


def test_bare_date_utc_row_is_dated():
    rep = report([H("AAA", 10)], [H("AAA", 15)],
                 ledger=[rate("AAA", "Buy", date_utc="2026-07-01")])   # no as_of
    c = by_sym(rep)["AAA"]
    assert c["verdict"] == "followed" and c["rating_asof"] == "2026-07-01"


def test_pre_sidecar_date_is_no_trigger():
    # no sidecars in-window → falls through to the rating axis (here: none → no-call)
    rep = report([H("AAA", 10)], [H("AAA", 15)], sidecars=[])
    assert by_sym(rep)["AAA"]["verdict"] == "no-call"


# ---- partial-snapshot gate ----

def test_partial_snapshot_suppresses_trim_exit_but_grades_adds():
    rep = report(
        [H("DROP", 10), H("CUT", 20), H("BUY", 5)],
        [H("CUT", 12), H("BUY", 9)],                         # DROP gone, CUT trimmed... but partial
        ledger=[rate("BUY", "Buy", as_of="2026-07-01")],
        new_skip=1)
    m = by_sym(rep)
    assert m["DROP"]["verdict"] == "unverifiable"            # a skipped account can look like a sell
    assert m["CUT"]["verdict"] == "unverifiable"
    assert m["BUY"]["verdict"] == "followed"                 # an add can't be a skip artifact


# ---- helpers + CLI exit-code contract ----

def test_trigger_dir_mapping():
    assert pd.trigger_dir("Add to position") == 1
    assert pd.trigger_dir("Trim 20%") == -1 and pd.trigger_dir("Sell") == -1 and pd.trigger_dir("Exit") == -1
    assert pd.trigger_dir("Stop trimming / re-rate") == 0 and pd.trigger_dir("re-rate") == 0
    assert pd.trigger_dir("") is None and pd.trigger_dir("ponder") is None


def test_load_sidecars_windows_and_tags(tmp_path):
    (tmp_path / "monitor-2026-07-04.json").write_text(json.dumps([trig("OLD", "Add", "2026-07-04")]))
    (tmp_path / "monitor-2026-07-06.json").write_text(json.dumps([trig("IN", "Trim", "2026-07-06")]))
    (tmp_path / "monitor-2026-07-07.json").write_text(json.dumps([trig("FUT", "Add", "2026-07-07")]))
    rows = pd.load_sidecars(str(tmp_path), "2026-07-05", "2026-07-06")   # (05, 06]
    assert [r["ticker"] for r in rows] == ["IN"] and rows[0]["date"] == "2026-07-06"


def _write_snap(d, date, holdings, schema=1, skipped=0):
    e = env(holdings, skipped)
    e["schema"] = schema
    (d / (date + ".json")).write_text(json.dumps(e))


def test_main_fewer_than_two_snapshots_exit_3(tmp_path):
    hd = tmp_path / "hist"; hd.mkdir()
    _write_snap(hd, "2026-07-06", [H("AAA", 10)])
    rc = pd.main([str(hd), str(tmp_path / "l.jsonl"), str(tmp_path), str(tmp_path / "delta.md")])
    assert rc == 3


def test_main_schema_mismatch_exit_5(tmp_path):
    hd = tmp_path / "hist"; hd.mkdir()
    _write_snap(hd, "2026-07-05", [H("AAA", 10)], schema=1)
    _write_snap(hd, "2026-07-06", [H("AAA", 15)], schema=2)              # drift
    rc = pd.main([str(hd), str(tmp_path / "l.jsonl"), str(tmp_path), str(tmp_path / "delta.md")])
    assert rc == 5


def test_main_usage_exit_2(tmp_path):
    assert pd.main([str(tmp_path)]) == 2


def test_main_writes_md_and_json_with_gap_note(tmp_path):
    hd = tmp_path / "hist"; hd.mkdir()
    _write_snap(hd, "2026-07-01", [H("AAA", 10)])
    _write_snap(hd, "2026-07-06", [H("AAA", 15)])                        # 5-day gap
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps(rate("AAA", "Buy", as_of="2026-06-30")) + "\n")
    out_md = tmp_path / "delta-2026-07-06.md"
    rc = pd.main([str(hd), str(ledger), str(tmp_path), str(out_md)])
    assert rc == 0
    md = out_md.read_text()
    assert "5-day gap" in md and "Portfolio delta — 2026-07-06 (vs 2026-07-01)" in md
    j = json.loads((tmp_path / "delta-2026-07-06.json").read_text())
    assert j["gap_days"] == 5 and by_sym(j)["AAA"]["verdict"] == "followed"
